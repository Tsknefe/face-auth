"""Start face-auth with one command (this is also the .exe entry point).

    python -m faceauth                  # starts on http://127.0.0.1:8100 and opens the demo
    python -m faceauth --port 9000 --no-browser
    python -m faceauth --host 0.0.0.0   # reachable from other machines (use HTTPS in front!)
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="face-auth", description="Face verification + liveness service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--no-browser", action="store_true", help="don't open the demo page")
    a = ap.parse_args(argv)

    import uvicorn
    from .api import create_app
    from .config import Settings

    cfg = Settings()
    print(f"[face-auth] data:   {cfg.data_dir.resolve()}")
    print(f"[face-auth] models: {cfg.model_dir.resolve()}")
    try:
        app = create_app(cfg=cfg)
    except Exception as e:  # most likely: models could not be downloaded
        print(f"[face-auth] could not start: {e}")
        if getattr(sys, "frozen", False):
            input("Press Enter to close...")
        return 1

    url = f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}/demo"
    print(f"[face-auth] running -> {url}   (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
