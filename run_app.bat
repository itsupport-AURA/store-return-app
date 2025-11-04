@echo off
REM ==============================================
REM Store Return / Damage Export - Run Script
REM ==============================================
cd /d "%~dp0"

echo.
echo ----------------------------------------------
echo   Starting Store Return / Damage Export App
echo ----------------------------------------------
echo.

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate

echo.
echo Installing/updating required dependencies...
pip install --upgrade -r requirements.txt >nul

echo.
echo Running app.py ...
echo (Press CTRL+C to stop the server)
echo ----------------------------------------------
python app.py

pause
