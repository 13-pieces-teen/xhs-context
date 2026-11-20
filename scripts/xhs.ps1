$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cliPath = Join-Path $projectRoot '.venv\Scripts\xhs-context.exe'
if (-not (Test-Path -LiteralPath $cliPath)) {
    throw 'Run scripts/install.ps1 first.'
}
& $cliPath @args
exit $LASTEXITCODE

