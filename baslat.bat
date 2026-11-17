@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title face-auth

echo [face-auth] Guncelleme kontrol ediliyor...
git pull --ff-only
if errorlevel 1 echo [face-auth] Guncelleme alinamadi, mevcut surumle devam ediliyor.

if not exist ".venv\Scripts\python.exe" (
    echo [face-auth] Ilk kurulum: sanal ortam olusturuluyor...
    python -m venv .venv
    if errorlevel 1 (
        echo [face-auth] Python bulunamadi. https://www.python.org adresinden Python 3.10+ kurun.
        pause
        exit /b 1
    )
)

echo [face-auth] Paketler kontrol ediliyor...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [face-auth] Paket kurulumu basarisiz.
    pause
    exit /b 1
)

echo [face-auth] Baslatiliyor... (kapatmak icin bu pencereyi kapatin veya Ctrl+C)
".venv\Scripts\python.exe" -m faceauth %*
if errorlevel 1 pause
