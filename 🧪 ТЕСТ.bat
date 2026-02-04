@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found!
    pause
    exit /b 1
)

:: Visible errors for debug
python dev_test\main_dev.py
pause
