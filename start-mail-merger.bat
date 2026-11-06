@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "app.py"
) else (
    echo Create the .venv and install requirements.txt first. See README.md.
    pause
)
