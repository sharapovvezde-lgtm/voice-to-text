@echo off
chcp 65001 >nul
cd /d "%~dp0"
title [DEV] Whisper Quick-Type - ТЕСТОВЫЙ РЕЖИМ
color 0E

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║     [DEV] ТЕСТОВЫЙ РЕЖИМ                  ║
echo  ║     Whisper Quick-Type + Meetings         ║
echo  ╚═══════════════════════════════════════════╝
echo.
echo  Консоль остаётся открытой для отладки.
echo  Все логи будут показаны здесь.
echo.

:: Проверка зависимостей для Meeting Recorder
pip show imageio-ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  [!] Устанавливаю зависимости для Meeting Recorder...
    pip install mss opencv-python Pillow imageio-ffmpeg pyaudiowpatch --quiet
    echo  [OK] Зависимости установлены
    echo.
)
pip show pyaudiowpatch >nul 2>&1
if errorlevel 1 (
    echo  [!] Устанавливаю pyaudiowpatch для записи системного звука...
    pip install pyaudiowpatch --quiet
    echo  [OK] pyaudiowpatch установлен
    echo.
)

:: Запуск DEV версии
python dev_test\main_dev.py

echo.
echo  ═══════════════════════════════════════════
echo  Приложение закрыто. Нажмите любую клавишу.
pause >nul
