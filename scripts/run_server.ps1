# Runs the NexTrade dashboard/scanner API continuously, restarting it
# automatically if the process ever exits (crash, unhandled exception,
# etc). Intended to be launched by a Windows Task Scheduler task at
# system startup, not run interactively.
#
# Logs go to logs\server_YYYY-MM-DD.log (one file per calendar day).
# Restart attempts are throttled to avoid a tight crash loop pinning
# the CPU if the app fails immediately on every launch.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

while ($true) {
    $LogFile = Join-Path $LogDir ("server_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
    $StartedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "[$StartedAt] Starting uvicorn..."

    python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --log-level info *>> $LogFile

    $ExitedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "[$ExitedAt] uvicorn exited (code $LASTEXITCODE). Restarting in 5 seconds..."
    Start-Sleep -Seconds 5
}
