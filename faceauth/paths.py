"""Where files live, both when run from source and when frozen into an .exe (PyInstaller)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# read-only bundled resources (web/, models/)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def default_data_dir() -> Path:
    """Writable place for the database and keys. From source: ./data.
    As an exe: %APPDATA%\\face-auth (so it survives updates of the exe)."""
    if FROZEN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "face-auth"
    return Path("./data")


def default_model_dir() -> Path:
    bundled = RESOURCE_DIR / "models"
    if bundled.is_dir() and any(bundled.glob("*.onnx")):
        return bundled
    return (default_data_dir() / "models") if FROZEN else Path("./models")


def web_dir() -> Path:
    return RESOURCE_DIR / "web"
