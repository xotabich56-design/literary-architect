@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo Для запуска установите Python 3.11 или новее с https://www.python.org/downloads/windows/
  echo При установке включите Python Launcher и Add Python to PATH.
  pause
  exit /b 1
)
py -3 app.py
pause
