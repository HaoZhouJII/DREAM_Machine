@echo off
setlocal

REM Start DREAM LED Control Panel using the project virtual environment.
REM Put this .bat file in:
REM   C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_LED_control

set "PROJECT_DIR=C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control"
set "LED_DIR=C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_LED_control"
set "VENV_PY=%PROJECT_DIR%\venv\Scripts\python.exe"

cd /d "%LED_DIR%"

if exist "%VENV_PY%" (
    "%VENV_PY%" DREAM_LED_control_panel.py
) else (
    echo Could not find venv Python at:
    echo %VENV_PY%
    echo Falling back to py...
    py DREAM_LED_control_panel.py
)

pause
