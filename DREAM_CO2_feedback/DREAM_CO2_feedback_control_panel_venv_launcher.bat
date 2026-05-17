@echo off
REM DREAM_CO2_feedback_control_panel_venv_launcher.bat
REM Launch DREAM CO2 feedback control panel using the DREAM_Env_Control virtual environment.

set "PROJECT_ROOT=C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control"
set "SCRIPT_DIR=C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_CO2_feedback"

echo ==========================================
echo DREAM CO2 Feedback Control Panel Launcher
echo Using virtual environment from DREAM_Env_Control
echo ==========================================
echo Project root:
echo %PROJECT_ROOT%
echo.
echo Script folder:
echo %SCRIPT_DIR%
echo.

cd /d "%SCRIPT_DIR%"

IF NOT EXIST "DREAM_CO2_feedback_control_panel.py" (
    echo ERROR: DREAM_CO2_feedback_control_panel.py was not found in:
    echo %SCRIPT_DIR%
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM Try common venv folder names under PROJECT_ROOT
REM ------------------------------------------------------------

set "PY_EXE="

IF EXIST "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PY_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
    goto FOUND_PYTHON
)

IF EXIST "%PROJECT_ROOT%\venv\Scripts\python.exe" (
    set "PY_EXE=%PROJECT_ROOT%\venv\Scripts\python.exe"
    goto FOUND_PYTHON
)

IF EXIST "%PROJECT_ROOT%\env\Scripts\python.exe" (
    set "PY_EXE=%PROJECT_ROOT%\env\Scripts\python.exe"
    goto FOUND_PYTHON
)

REM ------------------------------------------------------------
REM Fallback: try activated Python from PATH
REM ------------------------------------------------------------

where python >nul 2>nul
IF %ERRORLEVEL% EQU 0 (
    set "PY_EXE=python"
    goto FOUND_PYTHON
)

echo ERROR: Could not find Python in the virtual environment.
echo.
echo I checked:
echo   %PROJECT_ROOT%\.venv\Scripts\python.exe
echo   %PROJECT_ROOT%\venv\Scripts\python.exe
echo   %PROJECT_ROOT%\env\Scripts\python.exe
echo.
echo If your venv has another name, edit this BAT file and set PY_EXE manually.
echo Example:
echo   set "PY_EXE=C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\YOUR_VENV_NAME\Scripts\python.exe"
echo.
pause
exit /b 1

:FOUND_PYTHON
echo Using Python:
echo %PY_EXE%
echo.

"%PY_EXE%" DREAM_CO2_feedback_control_panel.py

echo.
echo Control panel closed.
pause
exit /b %ERRORLEVEL%
