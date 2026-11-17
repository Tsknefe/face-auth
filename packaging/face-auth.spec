# PyInstaller spec -> dist/face-auth.exe (one file, models + web bundled)
# Build:  python scripts/download_models.py models
#         pyinstaller packaging/face-auth.spec
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [ROOT + "/packaging/run_faceauth.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/web", "web"), (ROOT + "/models", "models")],
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("faceauth"),
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="face-auth",
    console=True,          # keep the console: it shows the URL and logs
    upx=False,
)
