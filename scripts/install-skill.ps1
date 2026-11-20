param([string]$Destination)
$ErrorActionPreference = 'Stop'
$taskProject = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $taskCodexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $Destination = Join-Path $taskCodexRoot 'skills\xhs-context'
}
$taskSource = Join-Path $taskProject 'skills\xhs-context'
if (Test-Path -LiteralPath $Destination) {
    $taskBackup = Join-Path $taskProject ('var\backups\skill-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $taskBackup) -Force | Out-Null
    Copy-Item -LiteralPath $Destination -Destination $taskBackup -Recurse -ErrorAction Stop
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Get-ChildItem -LiteralPath $taskSource -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force -ErrorAction Stop
}
Write-Host ('Installed xhs-context Skill: ' + $Destination)
