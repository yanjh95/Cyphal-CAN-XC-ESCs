@echo off
echo === Cyphal-CAN XC ESCs Setup (Windows) ===

:: Check if python is installed
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Error: Python could not be found. Please install Python 3 and add it to your PATH.
    exit /b 1
)

echo Creating virtual environment 'venv'...
python -m venv venv

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing requirements...
pip install -r requirements.txt

echo.
echo === Setup Complete! ===
echo To begin using the CLI, run the following command to activate your virtual environment:
echo     venv\Scripts\activate.bat
echo.
echo Then you can start the application:
echo     python cyphal-cli.py
pause
