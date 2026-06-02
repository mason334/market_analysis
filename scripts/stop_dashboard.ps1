$ROOT     = Split-Path -Parent $PSScriptRoot
$PID_FILE = Join-Path $ROOT "logs\dashboard.pid"

if (-not (Test-Path $PID_FILE)) {
    Write-Host "Dashboard is not running (no PID file found)."
    exit 0
}

$savedPid = (Get-Content $PID_FILE).Trim()
$proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue

if (-not $proc) {
    Write-Host "Dashboard process (PID $savedPid) is already stopped."
    Remove-Item $PID_FILE
    exit 0
}

Stop-Process -Id $savedPid -Force
Remove-Item $PID_FILE
Write-Host "Dashboard stopped (PID $savedPid)."
