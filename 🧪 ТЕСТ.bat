@echo off
chcp 65001 >nul
cd /d "%~dp0"
title [DEV] Whisper Quick-Type - ТЕСТОВЫЙ РЕЖИМ
color 0E

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║     [DEV] ТЕСТОВЫЙ РЕЖИМ                  ║
echo  ║     Whisper Quick-Type                    ║
echo  ╚═══════════════════════════════════════════╝
echo.
echo  Консоль остаётся открытой для отладки.
echo  Все логи будут показаны здесь.
echo.

python main.py

echo.
echo  ═══════════════════════════════════════════
echo  Приложение закрыто. Нажмите любую клавишу.
pause >nul
