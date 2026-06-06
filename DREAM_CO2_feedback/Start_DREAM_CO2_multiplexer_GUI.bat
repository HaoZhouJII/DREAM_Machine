@echo off
setlocal

REM Start DREAM CO2 Multiplexer GUI using the DREAM virtual environment.

set "PROJECT_DIR=C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control"
set "MUX_DIR=%PROJECT_DIR%\DREAM_CO2_multiplexer_control"
set "VENV_PY=%PROJECT_DIR%\venv\Scripts\python.exe"

cd /d "%MUX_DIR%"

if not exist "%VENV_PY%" (
    echo Python virtual environment not found:
    echo %VENV_PY%
    pause
    exit /b 1
)

if not exist "DREAM_CO2_multiplexer_GUI.py" (
    echo GUI script not found in:
    echo %CD%
    pause
    exit /b 1
)

echo Running DREAM CO2 Multiplexer GUI from: %CD%
"%VENV_PY%" DREAM_CO2_multiplexer_GUI.py
pause
