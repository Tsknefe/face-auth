"""Active liveness: the server asks for a random sequence of head movements and checks
them on the frames the client sends back.

Head pose is estimated from YuNet's 5 landmarks, relative to the user's own neutral
("center") frame, so it works for any face shape and camera angle.

Directions are from the USER's point of view, with a normal (non-mirrored) camera
image: when the user turns to their own left, their nose moves to the image's right.
"""
from __future__ import annotations

import math
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
    pitch > 0: user looks down, < 0: user looks up.
    pitch = log(eyes->nose / nose->mouth): the nose tip sticks out of the face plane, so
    tilting the head moves it towards the eye line (up) or the mouth line (down)."""
    r_eye, l_eye, nose, r_mouth, l_mouth = lm
    eye_mid = (r_eye + l_eye) / 2
    mouth_mid = (r_mouth + l_mouth) / 2
    iod = float(np.linalg.norm(l_eye - r_eye)) + 1e-6
    yaw = float(nose[0] - eye_mid[0]) / iod
    up = max(1e-3, float(nose[1] - eye_mid[1]))
    down = max(1e-3, float(mouth_mid[1] - nose[1]))
    pitch = math.log(up / down)
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


def _movement(action: str, y0: float, p0: float, o: FaceObs) -> float:
    """Signed amount of the requested movement (positive = in the right direction)."""
    y, p = head_pose(o.landmarks)
    return {"turn_left": y - y0, "turn_right": y0 - y,
            "look_down": p - p0, "look_up": p0 - p}[action]


def check(ch: Challenge, obs: list[list[FaceObs | None]], cfg) -> LivenessResult:
    """obs[i] = the frames captured during action i (one or more). For every action
    the best frame is used, so a movement that was held only briefly still counts."""
    if len(obs) != len(ch.actions):
        return LivenessResult(False, f"expected frames for {len(ch.actions)} steps, got {len(obs)}")
    for i, group in enumerate(obs):
        if not group:
            return LivenessResult(False, f"no frames for step {i} ({ch.actions[i]})")
        for o in group:
            if o is None:
                return LivenessResult(False, f"no face in step {i} ({ch.actions[i]}) - stay in the oval")
            if o.n_faces != 1:
                return LivenessResult(False, f"exactly one face required (step {i})")
            if min(o.bbox[2], o.bbox[3]) < cfg.min_face_px:
                return LivenessResult(False, f"face too small in step {i}, come closer")
            if o.real_score is not None and o.real_score < cfg.antispoof_threshold:
                return LivenessResult(False, f"presentation attack suspected (step {i})")

    # same person in every frame
    ref = obs[0][0].embedding
    for i, group in enumerate(obs):
        for o in group:
            if cosine(ref, o.embedding) < cfg.same_person_threshold:
                return LivenessResult(False, f"face changed between frames (step {i})")

    # neutral pose = the most frontal frame of the "center" step
    center = min(obs[0], key=lambda o: abs(head_pose(o.landmarks)[0]))
    y0, p0 = head_pose(center.landmarks)
    if abs(y0) > 0.25:
        return LivenessResult(False, "first step: look straight at the camera")
    chosen = [center]
    for i, (a, group) in enumerate(zip(ch.actions[1:], obs[1:]), 1):
        best = max(group, key=lambda o: _movement(a, y0, p0, o))
        amount = _movement(a, y0, p0, best)
        need = cfg.yaw_delta if a.startswith("turn") else cfg.pitch_delta
        if amount < need:
            return LivenessResult(False, f"movement '{a}' not detected (step {i}): "
                                         f"measured {amount:.2f}, need {need:.2f}")
        chosen.append(best)

    # identity template: frontal frame weighted more (it's the cleanest)
    embs = np.stack([o.embedding for o in chosen])
    w = np.array([2.0] + [1.0] * (len(chosen) - 1))[:, None]
    m = (embs * w).sum(0)
    return LivenessResult(True, embedding=(m / (np.linalg.norm(m) + 1e-9)).astype(np.float32))
