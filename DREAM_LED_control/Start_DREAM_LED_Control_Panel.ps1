# Start_DREAM_LED_Control_Panel.ps1
# Start DREAM LED Control Panel using the project virtual environment.

$PROJECT_DIR = "C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control"
$LED_DIR = "C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control\DREAM_LED_control"
$VENV_PY = Join-Path $PROJECT_DIR "venv\Scripts\python.exe"

Set-Location $LED_DIR

Write-Host "Running GUI from:"
Write-Host (Join-Path $LED_DIR "DREAM_LED_control_panel.py")
Write-Host ""

Get-Item "DREAM_LED_control_panel.py" | Format-List Name, FullName, LastWriteTime, Length

if (Test-Path $VENV_PY) {
    & $VENV_PY "DREAM_LED_control_panel.py"
} else {
    Write-Host "Could not find venv Python at:"
    Write-Host $VENV_PY
    Write-Host "Falling back to py..."
    py "DREAM_LED_control_panel.py"
}
