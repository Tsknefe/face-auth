"""Configuration, read from environment variables (prefix FACEAUTH_)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import default_data_dir, default_model_dir


def _env(name: str, default: str) -> str:
    return os.environ.get(f"FACEAUTH_{name}", default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(default_data_dir()))))
    model_dir: Path = field(default_factory=lambda: Path(_env("MODEL_DIR", str(default_model_dir()))))

    # Issuer name written into tokens; projects check it.
    issuer: str = field(default_factory=lambda: _env("ISSUER", "face-auth"))
    token_ttl_s: int = field(default_factory=lambda: int(_env("TOKEN_TTL", "300")))

    # Face matching (SFace cosine similarity; OpenCV's reference threshold is 0.363)
    match_threshold: float = field(default_factory=lambda: float(_env("MATCH_THRESHOLD", "0.42")))
    # all frames of one challenge must show the same person
    same_person_threshold: float = field(default_factory=lambda: float(_env("SAME_PERSON_THRESHOLD", "0.38")))

    # Liveness challenge
    challenge_ttl_s: int = field(default_factory=lambda: int(_env("CHALLENGE_TTL", "60")))
    challenge_actions: int = field(default_factory=lambda: int(_env("CHALLENGE_ACTIONS", "2")))
    yaw_delta: float = field(default_factory=lambda: float(_env("YAW_DELTA", "0.18")))
    pitch_delta: float = field(default_factory=lambda: float(_env("PITCH_DELTA", "0.12")))
    min_face_px: int = field(default_factory=lambda: int(_env("MIN_FACE_PX", "90")))

    # Optional passive anti-spoofing ONNX model (e.g. MiniFASNet). Empty = disabled.
    antispoof_model: str = field(default_factory=lambda: _env("ANTISPOOF_MODEL", ""))
    antispoof_threshold: float = field(default_factory=lambda: float(_env("ANTISPOOF_THRESHOLD", "0.6")))

    # Abuse protection
    max_failures: int = field(default_factory=lambda: int(_env("MAX_FAILURES", "5")))
    lockout_s: int = field(default_factory=lambda: int(_env("LOCKOUT", "900")))

    # API keys of client projects: "crypto:KEY1,blog:KEY2"
    api_keys_raw: str = field(default_factory=lambda: _env("API_KEYS", ""))

    @property
    def api_keys(self) -> dict[str, str]:
        """key -> project name"""
        out = {}
        for part in self.api_keys_raw.split(","):
            if ":" in part:
                name, key = part.split(":", 1)
                out[key.strip()] = name.strip()
        return out
