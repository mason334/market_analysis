$ErrorActionPreference = "Stop"

$ROOT     = Split-Path -Parent $PSScriptRoot
$VENV     = Join-Path $ROOT ".venv\Scripts"
$LOG_DIR  = Join-Path $ROOT "logs"
$PID_FILE = Join-Path $LOG_DIR "dashboard.pid"
$LOG_FILE = Join-Path $LOG_DIR "dashboard.log"
$ERR_FILE = Join-Path $LOG_DIR "dashboard_err.log"
$PORT     = 8504

if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR | Out-Null
}

# Check if already running
if (Test-Path $PID_FILE) {
    $existingPid = (Get-Content $PID_FILE).Trim()
    $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Dashboard already running (PID $existingPid). Open http://localhost:$PORT"
        exit 0
    }
    Remove-Item $PID_FILE
}

$streamlit  = Join-Path $VENV "streamlit.exe"
$dashboard  = Join-Path $ROOT "dashboard.py"

$proc = Start-Process `
    -FilePath $streamlit `
    -ArgumentList "run `"$dashboard`" --server.port $PORT --server.headless true" `
    -RedirectStandardOutput $LOG_FILE `
    -RedirectStandardError  $ERR_FILE `
    -PassThru `
    -WindowStyle Hidden

Start-Sleep -Milliseconds 1500

if ($proc.HasExited) {
    Write-Host "ERROR: Dashboard failed to start. Check $LOG_FILE and $ERR_FILE"
    exit 1
}

$proc.Id | Out-File -FilePath $PID_FILE -Encoding ascii -NoNewline
Write-Host "Dashboard started (PID $($proc.Id))"
Write-Host "URL : http://localhost:$PORT"
Write-Host "Log : $LOG_FILE"
Write-Host "Stop: scripts\stop_dashboard.ps1"
