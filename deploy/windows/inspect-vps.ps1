<#
.SYNOPSIS  Read-only inspection of the Windows VPS for LRB deployment. Changes nothing.
.EXAMPLE   powershell -ExecutionPolicy Bypass -File .\inspect-vps.ps1 > inspect-vps.txt
#>
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
Write-Utc "== OS =="
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture, LastBootUpTime | Format-List
Write-Utc "== Time sync (w32tm) =="
w32tm /query /status 2>&1
Write-Utc "== Time zone / UTC =="
Get-TimeZone | Format-List Id, BaseUtcOffset
Write-Utc ("UTC now: {0:o}" -f (Get-Date).ToUniversalTime())
Write-Utc "== Installed MT5 terminals (terminal64.exe) =="
$roots = @("$env:ProgramFiles", "${env:ProgramFiles(x86)}", "$env:LOCALAPPDATA", "C:\") | Where-Object { $_ -and (Test-Path $_) }
foreach ($r in $roots) {
    Get-ChildItem -Path $r -Filter terminal64.exe -Recurse -Depth 3 -ErrorAction SilentlyContinue |
        ForEach-Object { "{0}  (version {1})" -f $_.FullName, $_.VersionInfo.FileVersion }
}
Write-Utc "== Running MT5 terminals =="
Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object Id, StartTime, Path, SessionId | Format-Table -AutoSize
Write-Utc "== Interactive sessions (MT5 needs a desktop session that stays logged on) =="
query user 2>&1
Write-Utc "== MT5 data folders =="
Get-ChildItem (Join-Path $env:APPDATA 'MetaQuotes\Terminal') -Directory -ErrorAction SilentlyContinue | Select-Object Name, LastWriteTime | Format-Table -AutoSize
Write-Utc "== LRB common folder =="
if (Test-Path $LrbCommon) { Get-ChildItem $LrbCommon -Recurse | Select-Object FullName, Length, LastWriteTime | Format-Table -AutoSize } else { "not present: $LrbCommon" }
Write-Utc "== Python =="
foreach ($c in @('py -3.11 --version', 'python --version', 'uv --version')) { try { cmd /c $c 2>&1 } catch { "$c : not found" } }
Write-Utc "== Listening ports 8787 (LRB service) =="
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess
Write-Utc "== Scheduled tasks =="
Get-ScheduledTask -TaskName 'LRB-*' -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize
Write-Utc "== Disk =="
Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free | Format-Table -AutoSize
Write-Utc "Next: in MT5 run Scripts\LRB\LRB_InspectEnvironment on the target symbol chart (broker/symbol/session/tick facts)."
