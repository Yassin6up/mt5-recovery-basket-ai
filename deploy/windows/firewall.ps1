<# The service listens on loopback only. Add an explicit inbound BLOCK rule for its port as defence in depth. #>
param([int]$Port = 8787)
. "$PSScriptRoot\common.ps1"
try {
    if (-not (Get-NetFirewallRule -DisplayName "LRB block inbound $Port" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "LRB block inbound $Port" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Block | Out-Null
    }
    Write-Utc "Inbound TCP $Port blocked (service binds 127.0.0.1 only)."
} catch { Write-Utc "Could not add firewall rule (needs Administrator): $($_.Exception.Message)" }
