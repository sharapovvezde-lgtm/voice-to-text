@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo  ========================================
echo   [DEV] TEST MODE
echo   Whisper Quick-Type + Meetings
echo  ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found!
    pause
    exit /b 1
)

:: Check dependencies
pip show imageio-ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  [!] Installing dependencies...
    pip install mss opencv-python Pillow imageio-ffmpeg --quiet
    echo  [OK] Dependencies installed
    echo.
)

echo  Console stays open for debug.
echo  All logs will be shown here.
echo.

:: Run DEV version
python dev_test\main_dev.py

echo.
echo  ========================================
echo  App closed. Press any key.
pause >nul
