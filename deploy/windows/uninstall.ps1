<# Removes LRB scheduled tasks and the firewall rule. Keeps data, backups and the secret file. #>
. "$PSScriptRoot\common.ps1"
foreach ($t in 'LRB-Service', 'LRB-Health', 'LRB-Backup', 'LRB-MT5-Terminal') {
    Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
}
Get-NetFirewallRule -DisplayName 'LRB block inbound *' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Utc "Removed tasks. Data in C:\LRB\data and $LrbCommon left in place."
