# Shared helpers for LRB Windows deployment scripts. Dot-source: . "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Stop'
$Script:LrbMagic   = if ($env:LRB_MAGIC) { $env:LRB_MAGIC } else { '26101801' }
$Script:CommonDir  = Join-Path $env:APPDATA 'MetaQuotes\Terminal\Common\Files'
$Script:LrbCommon  = Join-Path $CommonDir 'LRB'
$Script:LrbMagicDir= Join-Path $LrbCommon $LrbMagic
$Script:SecretFile = Join-Path $LrbCommon 'hmac_secret.txt'

function Write-Utc([string]$msg) { Write-Host ("{0:yyyy-MM-ddTHH:mm:ssZ} {1}" -f (Get-Date).ToUniversalTime(), $msg) }

function Restrict-FileAcl([string]$path) {
    # Owner (current user) and SYSTEM only; inheritance removed.
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $path /inheritance:r /grant:r "${user}:(R,W)" "SYSTEM:(F)" | Out-Null
}
