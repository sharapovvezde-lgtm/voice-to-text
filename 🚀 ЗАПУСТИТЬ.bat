@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Если установка уже прошла - сразу запускаем
if exist ".installed" (
    start "" pythonw "%~dp0main.py"
    exit /b 0
)

:: ===== ПЕРВЫЙ ЗАПУСК =====
title Whisper Quick-Type - Установка
color 0A

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║      WHISPER QUICK-TYPE                   ║
echo  ║      Первоначальная установка             ║
echo  ╚═══════════════════════════════════════════╝
echo.

:: ШАГ 1: PYTHON
echo [1/4] Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  Установка Python...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>nul
    if errorlevel 1 (
        powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe' -OutFile '%TEMP%\python.exe'"
        if exist "%TEMP%\python.exe" (
            "%TEMP%\python.exe" /quiet InstallAllUsers=0 PrependPath=1
            timeout /t 30 /nobreak >nul
            del "%TEMP%\python.exe" >nul 2>&1
        ) else (
            echo  ОШИБКА! Установите Python вручную: python.org
            pause
            exit /b 1
        )
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
)
echo  OK

:: ШАГ 2: БИБЛИОТЕКИ
echo [2/4] Установка библиотек (3-5 минут)...
pip install -q PyQt6 pynput sounddevice scipy numpy pyperclip pyautogui 2>nul
pip install -q mss opencv-python imageio-ffmpeg 2>nul
echo  OK

:: ШАГ 3: WHISPER
echo [3/4] Установка Whisper (2-3 минуты)...
pip install -q openai-whisper 2>nul
echo  OK

:: ШАГ 4: ЯРЛЫК
echo [4/4] Создание ярлыка...

:: VBS для тихого запуска
echo Set WshShell = CreateObject("WScript.Shell") > "%~dp0launch.vbs"
echo WshShell.CurrentDirectory = "%~dp0" >> "%~dp0launch.vbs"
echo WshShell.Run "pythonw " ^& Chr(34) ^& "%~dp0main.py" ^& Chr(34), 0, False >> "%~dp0launch.vbs"

:: Ярлык на рабочем столе
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Whisper Quick-Type.lnk'); $s.TargetPath = '%~dp0launch.vbs'; $s.WorkingDirectory = '%~dp0'; $s.Save()"

:: Маркер установки
echo ok > ".installed"
echo  OK

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║         УСТАНОВКА ЗАВЕРШЕНА!              ║
echo  ╠═══════════════════════════════════════════╣
echo  ║  Ярлык на рабочем столе создан            ║
echo  ║  Горячие клавиши: CTRL + Z + X            ║
echo  ╚═══════════════════════════════════════════╝
echo.
timeout /t 3 >nul

start "" pythonw "%~dp0main.py"
