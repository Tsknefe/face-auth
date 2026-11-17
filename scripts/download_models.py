"""Download the face models into ./models (used by the exe build and the Docker image)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from faceauth.engine import MODELS, ensure_model  # noqa: E402

target = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
for key in MODELS:
    p = ensure_model(target, key)
    print(f"{p}  {p.stat().st_size / 1e6:.1f} MB")
