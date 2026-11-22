param([switch]$WithoutOcr, [string]$Library, [string]$SkillDestination)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
}
Push-Location -LiteralPath $projectRoot
try {
    # Body and image acquisition do not require OCR models.
    & uv sync --frozen --link-mode copy
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & (Join-Path $projectRoot '.venv\Scripts\xhs-context.exe') doctor
    if ($LASTEXITCODE -ne 0) { throw 'Environment checks failed.' }
    $setupArgs = @('setup', '--extension-dir', (Join-Path $projectRoot 'browser-extension'))
    if ($Library) { $setupArgs += @('--library', $Library) }
    & (Join-Path $projectRoot '.venv\Scripts\xhs-context.exe') @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Browser integration setup failed.' }
    & (Join-Path $PSScriptRoot 'install-skill.ps1') -Destination $SkillDestination
    Write-Host 'Ready. Load the browser-extension folder in Chrome or Edge once.'
    Write-Host 'Then open your saved collection and click the extension. No server or pairing is needed.'
} finally {
    Pop-Location
}

