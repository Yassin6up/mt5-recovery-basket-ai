<#
.SYNOPSIS  Install the LRB Python decision service on the SAME Windows host as MT5 (loopback only).
.DESCRIPTION
  - creates a virtualenv and installs hash-pinned dependencies (requirements.lock)
  - creates the shared HMAC secret in <Common>\Files\LRB\hmac_secret.txt (ACL: you + SYSTEM) if missing
  - writes service.env (no secrets inside; it points to the secret file)
  - registers scheduled tasks: LRB-Service (at startup, auto-restart), LRB-Health (every 5 min),
    LRB-Backup (daily)
  The service has NO order-execution authority. MT5 terminal startup is separate: see mt5-autostart.ps1.
.EXAMPLE   powershell -ExecutionPolicy Bypass -File .\install-service.ps1 -RepoDir C:\LRB\TRading
#>
param(
    [string]$RepoDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$DataDir = 'C:\LRB\data',
    [int]$Port = 8787,
    [string]$Python = 'py -3.11'
)
. "$PSScriptRoot\common.ps1"
$py = Join-Path $RepoDir 'python'
New-Item -ItemType Directory -Force -Path $DataDir, (Join-Path $DataDir 'logs'), (Join-Path $DataDir 'backups'), $LrbCommon, $LrbMagicDir | Out-Null

Write-Utc "Creating virtualenv"
if (-not (Test-Path "$py\.venv\Scripts\python.exe")) { cmd /c "$Python -m venv `"$py\.venv`"" ; if ($LASTEXITCODE) { throw "venv failed" } }
& "$py\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$py\.venv\Scripts\python.exe" -m pip install --require-hashes -r "$py\requirements.lock"
if ($LASTEXITCODE) { throw "dependency install failed" }
& "$py\.venv\Scripts\python.exe" -m pip install --no-deps -e "$py"
if ($LASTEXITCODE) { throw "package install failed" }

if (-not (Test-Path $SecretFile)) {
    Write-Utc "Generating HMAC secret (never printed)"
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [IO.File]::WriteAllText($SecretFile, [Convert]::ToBase64String($bytes))
}
Restrict-FileAcl $SecretFile

$envFile = Join-Path $DataDir 'service.env'
@"
LRB_HMAC_SECRET_FILE=$SecretFile
LRB_KEY_ID=ea1
LRB_DB_PATH=$DataDir\lrb_service.db
LRB_BIND_HOST=127.0.0.1
LRB_PORT=$Port
LRB_ALLOWED_SYMBOLS=EURUSD
LRB_DECISION_TTL_SEC=60
LRB_MAX_SKEW_SEC=30
LRB_MAX_DATA_AGE_SEC=1200
LRB_RETENTION_DAYS=30
LRB_LOG_LEVEL=INFO
# LRB_MODEL_PATH=$DataDir\entry_model.json   (only after out-of-sample validation)
# LRB_DASHBOARD_TOKEN=...                    (only needed if the dashboard is reached through a tunnel)
"@ | Set-Content -Encoding ASCII $envFile
Restrict-FileAcl $envFile

$runner = Join-Path $PSScriptRoot 'run-service.ps1'
$health = Join-Path $PSScriptRoot 'healthcheck.ps1'
$backup = Join-Path $PSScriptRoot 'backup.ps1'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -MultipleInstances IgnoreNew

$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -RepoDir `"$RepoDir`" -DataDir `"$DataDir`""
Register-ScheduledTask -TaskName 'LRB-Service' -Action $a -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal -Settings $settings -Force | Out-Null

$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$health`" -DataDir `"$DataDir`" -Port $Port"
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'LRB-Health' -Action $a -Trigger $t -Principal $principal -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew) -Force | Out-Null

$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backup`" -RepoDir `"$RepoDir`" -DataDir `"$DataDir`""
Register-ScheduledTask -TaskName 'LRB-Backup' -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At '00:30') -Principal $principal -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) -Force | Out-Null

& "$PSScriptRoot\firewall.ps1" -Port $Port
Start-ScheduledTask -TaskName 'LRB-Service'
Write-Utc "Installed. Check: Invoke-RestMethod http://127.0.0.1:$Port/health"
Write-Utc "Dashboard (on the VPS only): http://127.0.0.1:$Port/dashboard"
Write-Utc "MT5: allow WebRequest for http://127.0.0.1:$Port (Tools > Options > Expert Advisors)."
