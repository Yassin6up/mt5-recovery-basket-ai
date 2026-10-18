<# Configure Windows Time to sync with public NTP and force a resync (run as Administrator). #>
. "$PSScriptRoot\common.ps1"
w32tm /config /manualpeerlist:"time.windows.com,0x9 pool.ntp.org,0x9" /syncfromflags:manual /update
Restart-Service w32time
w32tm /resync /force
w32tm /query /status
Write-Utc "The service rejects requests with clock skew > 30s; the EA uses TimeGMT() derived from this clock."
