<#
Health check (LRB-Health task, every 5 minutes). Writes to the Windows Application event log.
- restarts the LRB-Service task if /health fails
- WARNS if terminal64.exe is not running or status.json is stale. It does not restart MT5:
  the terminal is a desktop application that needs the logged-on trading session (see mt5-autostart.ps1).
#>
param([string]$DataDir = 'C:\LRB\data', [int]$Port = 8787, [int]$StatusMaxAgeSec = 60)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$src = 'LRB'
if (-not [System.Diagnostics.EventLog]::SourceExists($src)) { try { New-EventLog -LogName Application -Source $src } catch {} }
function Ev([string]$level, [int]$id, [string]$msg) {
    try { Write-EventLog -LogName Application -Source $src -EntryType $level -EventId $id -Message $msg } catch {}
    Add-Content (Join-Path $DataDir 'logs\health.log') ("{0:o} {1} {2}" -f (Get-Date).ToUniversalTime(), $level, $msg)
}
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($h.status -ne 'ok') { throw "status=$($h.status)" }
} catch {
    Ev 'Error' 1001 "LRB service health failed: $($_.Exception.Message). Restarting LRB-Service."
    Stop-ScheduledTask -TaskName 'LRB-Service' -ErrorAction SilentlyContinue
    Start-Sleep 2
    Start-ScheduledTask -TaskName 'LRB-Service'
}
if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
    Ev 'Error' 1002 'MT5 terminal64.exe is NOT running. Existing positions are unmanaged until it is restarted (see docs/OPERATIONS.md).'
}
$status = Join-Path $LrbMagicDir 'status.json'
if (Test-Path $status) {
    $age = ((Get-Date) - (Get-Item $status).LastWriteTime).TotalSeconds
    if ($age -gt $StatusMaxAgeSec) { Ev 'Warning' 1003 "LRB_EA status.json is stale (${age}s). EA stopped, AutoTrading off, or terminal frozen." }
} else { Ev 'Warning' 1004 "status.json not found at $status (EA not attached?)" }
$hb = Join-Path $LrbMagicDir 'bridge_heartbeat.txt'
if ((Test-Path $hb) -and (((Get-Date) - (Get-Item $hb).LastWriteTime).TotalSeconds -gt 60)) {
    Ev 'Warning' 1005 'LRB_AIBridge heartbeat stale: AI-filtered entries are blocked (fail-safe).'
}
