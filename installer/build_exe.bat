\
@echo off
setlocal
cd /d %~dp0\..
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10+ from python.org
  pause
  exit /b 1
)

REM build venv
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install pyinstaller

REM build exe
pyinstaller --noconfirm --clean --onefile --name "AIReception" src\main.py

echo.
echo Done. EXE in dist\AIReception.exe
pause
