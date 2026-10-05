@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer first.
  pause
  exit /b 1
)
py -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo Flask is not installed. Run: py -m pip install flask
  pause
  exit /b 1
)
start "Printing Dashboard" http://127.0.0.1:8787
py app.py
