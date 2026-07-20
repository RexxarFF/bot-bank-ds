@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && set "PYTHON=py" || set "PYTHON=python"
if not exist ".venv\Scripts\python.exe" %PYTHON% -m venv .venv
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
.venv\Scripts\python.exe bot.py
if errorlevel 1 pause
