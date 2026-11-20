param([string]$Library, [int]$Port = 4319, [switch]$WithoutOcr)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cliPath = Join-Path $projectRoot '.venv\Scripts\xhs-context.exe'
$libraryArgs = @()
if ($Library) { $libraryArgs = @('--library', $Library) }
& $cliPath @libraryArgs init
if ($LASTEXITCODE -ne 0) { throw 'Library initialization failed.' }
$syncArgs = $libraryArgs + @('sync', 'serve', '--port', $Port.ToString())
if ($WithoutOcr) { $syncArgs += '--no-ocr' }
& $cliPath @syncArgs
exit $LASTEXITCODE

