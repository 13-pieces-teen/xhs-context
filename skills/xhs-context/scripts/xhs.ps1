$ErrorActionPreference = 'Stop'
# Resolve the installed CLI from user configuration; independent of the current directory.
$taskConfigPath = Join-Path $env:LOCALAPPDATA 'xhs-context\config.json'
if (-not (Test-Path -LiteralPath $taskConfigPath)) {
    throw 'Install xhs-context with its 安装.cmd before querying the archive.'
}
$taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskCli = $taskConfig.cli_path
if (-not $taskCli -and $taskConfig.extension_dir) {
    $taskCli = Join-Path (Split-Path -Parent $taskConfig.extension_dir) '.venv\Scripts\xhs-context.exe'
}
if (-not $taskCli -or -not (Test-Path -LiteralPath $taskCli -PathType Leaf)) {
    throw 'The configured xhs-context CLI is missing. Run the project installer again.'
}
& $taskCli @args
exit $LASTEXITCODE
