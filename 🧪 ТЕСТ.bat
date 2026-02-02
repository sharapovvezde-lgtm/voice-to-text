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
pip show moviepy >nul 2>&1
if errorlevel 1 (
    echo  [!] Устанавливаю зависимости для Meeting Recorder...
    echo      Это может занять 2-3 минуты...
    pip install mss opencv-python Pillow moviepy --quiet
    echo  [OK] Зависимости установлены
    echo.
)

:: Запуск DEV версии
python dev_test\main_dev.py

echo.
echo  ═══════════════════════════════════════════
echo  Приложение закрыто. Нажмите любую клавишу.
pause >nul
