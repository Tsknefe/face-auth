"""Active liveness: the server asks for a random sequence of head movements and checks
them on the frames the client sends back.

Head pose is estimated from YuNet's 5 landmarks, relative to the user's own neutral
("center") frame, so it works for any face shape and camera angle.

Directions are from the USER's point of view, with a normal (non-mirrored) camera
image: when the user turns to their own left, their nose moves to the image's right.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

import numpy as np

from .engine import FaceObs, cosine

ACTIONS = ("turn_left", "turn_right", "look_up", "look_down")

ACTION_TEXT_TR = {
    "center": "Kameraya düz bakın",
    "turn_left": "Başınızı sola çevirin",
    "turn_right": "Başınızı sağa çevirin",
    "look_up": "Yukarı bakın",
    "look_down": "Aşağı bakın",
}


def head_pose(lm: np.ndarray) -> tuple[float, float]:
    """(yaw, pitch) proxies, normalised by inter-ocular distance.
    yaw > 0: nose towards image right (user turned to their left).
    pitch > 0: nose lower relative to eyes (user looks down)."""
    r_eye, l_eye, nose, r_mouth, l_mouth = lm
    eye_mid = (r_eye + l_eye) / 2
    mouth_mid = (r_mouth + l_mouth) / 2
    iod = float(np.linalg.norm(l_eye - r_eye)) + 1e-6
    yaw = float(nose[0] - eye_mid[0]) / iod
    # nose position between eyes (0) and mouth (1)
    face_len = float(mouth_mid[1] - eye_mid[1]) + 1e-6
    pitch = float(nose[1] - eye_mid[1]) / face_len
    return yaw, pitch


@dataclass
class Challenge:
    id: str
    user_id: str
    purpose: str                    # "enroll" | "verify"
    scope: str                      # what the token will allow, e.g. "withdraw"
    project: str
    actions: list[str]              # always starts with "center"
    created: float = field(default_factory=time.time)
    used: bool = False


def new_challenge(user_id: str, purpose: str, scope: str, project: str, n_actions: int) -> Challenge:
    rng = secrets.SystemRandom()
    acts = rng.sample(ACTIONS, k=max(1, min(n_actions, len(ACTIONS))))
    return Challenge(id=secrets.token_urlsafe(24), user_id=user_id, purpose=purpose,
                     scope=scope, project=project, actions=["center"] + acts)


@dataclass
class LivenessResult:
    ok: bool
    reason: str = ""
    embedding: np.ndarray | None = None      # mean embedding of all frames (if ok)


def check(ch: Challenge, obs: list[FaceObs | None], cfg) -> LivenessResult:
    if len(obs) != len(ch.actions):
        return LivenessResult(False, f"expected {len(ch.actions)} frames, got {len(obs)}")
    for i, o in enumerate(obs):
        if o is None:
            return LivenessResult(False, f"no face in frame {i} ({ch.actions[i]})")
        if o.n_faces != 1:
            return LivenessResult(False, f"exactly one face required (frame {i})")
        if min(o.bbox[2], o.bbox[3]) < cfg.min_face_px:
            return LivenessResult(False, f"face too small in frame {i}, come closer")
        if o.real_score is not None and o.real_score < cfg.antispoof_threshold:
            return LivenessResult(False, f"presentation attack suspected (frame {i})")

    # same person in every frame
    ref = obs[0].embedding
    for i, o in enumerate(obs[1:], 1):
        if cosine(ref, o.embedding) < cfg.same_person_threshold:
            return LivenessResult(False, f"face changed between frames (frame {i})")

    y0, p0 = head_pose(obs[0].landmarks)
    if abs(y0) > 0.25:
        return LivenessResult(False, "first frame must look straight at the camera")
    for i, (a, o) in enumerate(zip(ch.actions[1:], obs[1:]), 1):
        y, p = head_pose(o.landmarks)
        dy, dp = y - y0, p - p0
        ok = {
            "turn_left": dy > cfg.yaw_delta,
            "turn_right": dy < -cfg.yaw_delta,
            "look_down": dp > cfg.pitch_delta,
            "look_up": dp < -cfg.pitch_delta,
        }[a]
        if not ok:
            return LivenessResult(False, f"movement '{a}' not detected (frame {i})")

    # identity template: frontal frame weighted more (it's the cleanest)
    embs = np.stack([o.embedding for o in obs])
    w = np.array([2.0] + [1.0] * (len(obs) - 1))[:, None]
    m = (embs * w).sum(0)
    return LivenessResult(True, embedding=(m / (np.linalg.norm(m) + 1e-9)).astype(np.float32))
