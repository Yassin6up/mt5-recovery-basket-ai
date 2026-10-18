<# Daily backup: online SQLite backup + EA state/journal files. Keeps 14 copies. #>
param([string]$RepoDir, [string]$DataDir = 'C:\LRB\data', [int]$Keep = 14)
. "$PSScriptRoot\common.ps1"
$dest = Join-Path $DataDir 'backups'
& (Join-Path $RepoDir 'python\.venv\Scripts\python.exe') -m lrb.service.backup (Join-Path $DataDir 'lrb_service.db') $dest $Keep
$stamp = "{0:yyyyMMddTHHmmssZ}" -f (Get-Date).ToUniversalTime()
$eaDest = Join-Path $dest "ea_$stamp"
if (Test-Path $LrbMagicDir) {
    New-Item -ItemType Directory -Force $eaDest | Out-Null
    Get-ChildItem $LrbMagicDir -File | Where-Object { $_.Extension -in '.txt', '.csv', '.json' } | Copy-Item -Destination $eaDest
}
Get-ChildItem $dest -Directory -Filter 'ea_*' | Sort-Object Name | Select-Object -SkipLast $Keep | Remove-Item -Recurse -Force
Write-Utc "backup done -> $dest (the HMAC secret is intentionally NOT backed up here)"
