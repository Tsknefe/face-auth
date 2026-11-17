"""Face engine: detection + landmarks (YuNet), embeddings (SFace), optional anti-spoofing.

Only depends on OpenCV + numpy. Models are downloaded from the official OpenCV Zoo
on first use (~38 MB in total) into FACEAUTH_MODEL_DIR.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
              "face_recognition_sface_2021dec.onnx"),
}


@dataclass
class FaceObs:
    """What the engine sees in one frame."""
    bbox: tuple[float, float, float, float]          # x, y, w, h
    landmarks: np.ndarray                              # 5x2: r-eye, l-eye, nose, r-mouth, l-mouth
    embedding: np.ndarray                              # L2-normalised
    score: float                                       # detector confidence
    real_score: Optional[float] = None                 # anti-spoofing "real" probability
    n_faces: int = 1


class Engine(Protocol):
    def analyze(self, image_bytes: bytes) -> Optional[FaceObs]: ...


def ensure_model(model_dir: Path, key: str) -> Path:
    name, url = MODELS[key]
    path = model_dir / name
    if not path.exists() or path.stat().st_size < 100_000:
        model_dir.mkdir(parents=True, exist_ok=True)
        print(f"[face-auth] downloading {name} ...")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    return path


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class OpenCVEngine:
    def __init__(self, model_dir: Path, antispoof_model: str = ""):
        import cv2
        self.cv2 = cv2
        det = ensure_model(model_dir, "yunet")
        rec = ensure_model(model_dir, "sface")
        self.detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), 0.8, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(rec), "")
        self.antispoof = None
        if antispoof_model:
            import onnxruntime as ort
            self.antispoof = ort.InferenceSession(antispoof_model, providers=["CPUExecutionProvider"])

    def _decode(self, image_bytes: bytes):
        arr = np.frombuffer(image_bytes, np.uint8)
        img = self.cv2.imdecode(arr, self.cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("image could not be decoded")
        h, w = img.shape[:2]
        if max(h, w) > 1280:                     # keep it fast
            s = 1280 / max(h, w)
            img = self.cv2.resize(img, (int(w * s), int(h * s)), interpolation=self.cv2.INTER_AREA)
        return img

    def _real_score(self, img, bbox) -> float:
        """MiniFASNet-style model: crop 2.7x around the face, 80x80, softmax, class 1 = real."""
        cv2 = self.cv2
        x, y, w, h = bbox
        cx, cy, s = x + w / 2, y + h / 2, max(w, h) * 2.7
        M = np.array([[80 / s, 0, 40 - 80 / s * cx], [0, 80 / s, 40 - 80 / s * cy]], np.float32)
        crop = cv2.warpAffine(img, M, (80, 80), borderMode=cv2.BORDER_REPLICATE)
        inp = crop.astype(np.float32).transpose(2, 0, 1)[None]
        out = self.antispoof.run(None, {self.antispoof.get_inputs()[0].name: inp})[0][0]
        e = np.exp(out - out.max())
        p = e / e.sum()
        return float(p[1]) if len(p) > 1 else float(p[0])

    def analyze(self, image_bytes: bytes) -> Optional[FaceObs]:
        img = self._decode(image_bytes)
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        if faces is None or len(faces) == 0:
            return None
        best = max(faces, key=lambda f: f[2] * f[3])
        aligned = self.recognizer.alignCrop(img, best)
        emb = self.recognizer.feature(aligned).flatten().astype(np.float32)
        emb /= np.linalg.norm(emb) + 1e-9
        bbox = tuple(float(v) for v in best[:4])
        obs = FaceObs(bbox=bbox, landmarks=best[4:14].reshape(5, 2).astype(np.float32),
                      embedding=emb, score=float(best[14]), n_faces=len(faces))
        if self.antispoof is not None:
            obs.real_score = self._real_score(img, bbox)
        return obs
