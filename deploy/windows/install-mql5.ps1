<# Copy the MQL5 sources into an MT5 data folder (File > Open Data Folder in MT5 shows the path). Then compile in MetaEditor. #>
param([Parameter(Mandatory)][string]$Mt5DataDir, [string]$RepoDir = (Resolve-Path "$PSScriptRoot\..\..").Path)
. "$PSScriptRoot\common.ps1"
$src = Join-Path $RepoDir 'mql5'
$dst = Join-Path $Mt5DataDir 'MQL5'
if (-not (Test-Path $dst)) { throw "not an MT5 data folder: $Mt5DataDir" }
foreach ($sub in 'Experts\LRB', 'Include\LRB', 'Scripts\LRB', 'Presets') {
    New-Item -ItemType Directory -Force (Join-Path $dst $sub) | Out-Null
    Copy-Item (Join-Path $src "$sub\*") (Join-Path $dst $sub) -Force
}
Write-Utc "Copied. Compile in MetaEditor (F7): Experts\LRB\LRB_EA.mq5, Experts\LRB\LRB_AIBridge.mq5, Scripts\LRB\*.mq5"
$me = Join-Path (Split-Path (Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object -First 1).Path -ErrorAction SilentlyContinue) 'metaeditor64.exe'
if ($me -and (Test-Path $me)) {
    foreach ($f in 'Experts\LRB\LRB_EA.mq5', 'Experts\LRB\LRB_AIBridge.mq5', 'Scripts\LRB\LRB_InspectEnvironment.mq5', 'Scripts\LRB\LRB_ExportTicks.mq5', 'Scripts\LRB\LRB_KillSwitch.mq5') {
        & $me /compile:"$(Join-Path $dst $f)" /inc:"$dst" /log | Out-Null
        Write-Utc "compiled $f (see .log next to the file for errors)"
    }
}
