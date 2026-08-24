@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Please run install_windows.ps1 first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python jarvis.py
