<# Runs the service in the foreground (used by the LRB-Service scheduled task). Logs to DataDir\logs (UTC JSON). #>
param([string]$RepoDir, [string]$DataDir = 'C:\LRB\data')
. "$PSScriptRoot\common.ps1"
Get-Content (Join-Path $DataDir 'service.env') | Where-Object { $_ -match '^\s*[A-Z_]+=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), 'Process')
}
$log = Join-Path $DataDir ("logs\service-{0:yyyyMMdd}.log" -f (Get-Date).ToUniversalTime())
Get-ChildItem (Join-Path $DataDir 'logs') -Filter 'service-*.log' | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item -ErrorAction SilentlyContinue
& (Join-Path $RepoDir 'python\.venv\Scripts\lrb-service.exe') *>> $log
exit $LASTEXITCODE   # non-zero => Task Scheduler restarts it (RestartCount/RestartInterval)
