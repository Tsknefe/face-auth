"""PyInstaller entry point for face-auth.exe"""
import sys

from faceauth.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
