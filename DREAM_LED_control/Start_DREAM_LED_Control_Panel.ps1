# Start_DREAM_LED_Control_Panel.ps1
# PowerShell launcher for DREAM LED Control Panel.

$PROJECT_DIR = "C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control"
$LED_DIR = "C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_LED_control"
$VENV_PY = Join-Path $PROJECT_DIR "venv\Scripts\python.exe"

Set-Location $LED_DIR

if (Test-Path $VENV_PY) {
    & $VENV_PY "DREAM_LED_control_panel.py"
} else {
    Write-Host "Could not find venv Python at: $VENV_PY"
    Write-Host "Falling back to py..."
    py "DREAM_LED_control_panel.py"
}
