<#
.SYNOPSIS  Start the MT5 terminal automatically when the trading user logs on.
.DESCRIPTION
  The MT5 terminal is a desktop application: it must run inside a logged-on interactive session.
  Python service supervision does NOT keep MT5 running. This script registers an "at logon" task
  for the current user that launches terminal64.exe. Combine with:
   1. Windows automatic logon for the trading user (e.g. Sysinternals Autologon, which stores the
      password LSA-encrypted) so a VPS reboot returns to a logged-on desktop.
   2. Disconnect RDP with the window close button or `tscon` - do NOT "Sign out"; signing out
      terminates MT5.
   3. In MT5: open the EURUSD M15 chart with LRB_EA and a second chart with LRB_AIBridge, enable
      Algo Trading, then File > Profiles > Save As "LRB". MT5 restores the last profile and the
      attached EAs (and the Algo Trading button state) on restart.
   4. Tick "Save password" at login so the terminal reconnects after restart. Never put the
      account password in a script or .ini file in this repository.
.EXAMPLE   .\mt5-autostart.ps1 -TerminalPath 'C:\Program Files\MetaTrader 5\terminal64.exe'
#>
param([Parameter(Mandatory)][string]$TerminalPath, [string]$Profile = 'LRB')
. "$PSScriptRoot\common.ps1"
if (-not (Test-Path $TerminalPath)) { throw "terminal not found: $TerminalPath" }
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$a = New-ScheduledTaskAction -Execute $TerminalPath -Argument "/profile:$Profile"
$t = New-ScheduledTaskTrigger -AtLogOn -User $user
$t.Delay = 'PT30S'
$p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'LRB-MT5-Terminal' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Write-Utc "Registered LRB-MT5-Terminal (at logon of $user). Configure auto-logon separately (see help)."
