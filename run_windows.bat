\
@echo off
setlocal
cd /d %~dp0
echo [AIReception] Starting...
echo Log will be saved to run.log
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found.
  echo Please install Python 3.10+ from python.org and tick "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

python -V
echo.

REM run and capture logs (keep window open even if success)
python src\main.py 1>>run.log 2>&1
set EXITCODE=%ERRORLEVEL%

echo.
echo [AIReception] Exit code: %EXITCODE%
echo If nothing showed up, open run.log to see details.
echo.
pause
exit /b %EXITCODE%
