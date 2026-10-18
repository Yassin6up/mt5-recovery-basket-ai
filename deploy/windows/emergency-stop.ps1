<#
.SYNOPSIS  EMERGENCY: tell LRB_EA to close its basket (FLATTEN) or stop adding exposure (HALT).
.DESCRIPTION
  Writes <Common>\Files\LRB\<magic>\KILL. LRB_EA polls it every 0.5 s and, for FLATTEN, closes
  every position with its magic number on its symbol (never manual trades or other EAs).
  This requires the MT5 terminal to be running and connected. If it is not, close positions
  manually in MT5 (or from the broker's web/mobile terminal). See docs/OPERATIONS.md.
.EXAMPLE   .\emergency-stop.ps1 -Mode FLATTEN
.EXAMPLE   .\emergency-stop.ps1 -Mode CLEAR      # only after review
#>
param([ValidateSet('FLATTEN', 'HALT', 'CLEAR')][string]$Mode = 'FLATTEN', [switch]$StopService)
. "$PSScriptRoot\common.ps1"
New-Item -ItemType Directory -Force $LrbMagicDir | Out-Null
$kill = Join-Path $LrbMagicDir 'KILL'
if ($Mode -eq 'CLEAR') {
    Remove-Item $kill -ErrorAction SilentlyContinue
    Write-Utc "KILL file removed. ALSO run Scripts\LRB\LRB_KillSwitch with InpMode=KILL_NONE in MT5 to clear the global variable."
} else {
    Set-Content -Path $kill -Value $Mode -Encoding ASCII
    Write-Utc "KILL=$Mode written to $kill"
}
if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
    Write-Utc "WARNING: terminal64.exe is NOT running - the EA cannot act. Close positions manually NOW (MT5 or broker web terminal)."
}
if ($StopService) { Stop-ScheduledTask -TaskName 'LRB-Service'; Write-Utc 'LRB-Service stopped (AI entries now fail-safe blocked).' }
Start-Sleep 3
$status = Join-Path $LrbMagicDir 'status.json'
if (Test-Path $status) { Get-Content $status | ConvertFrom-Json | Select-Object time_utc, kill, @{n='phase';e={$_.basket.phase}}, @{n='net_pnl';e={$_.basket.net_pnl}} | Format-List }
