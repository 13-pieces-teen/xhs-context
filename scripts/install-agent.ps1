# Non-interactive installation and local acceptance checks for an Agent.
# stdout contains one JSON result; installation progress is sent to stderr.
param([switch]$CheckOnly, [string]$Library, [string]$SkillDestination)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Invoke-JsonCommand {
    param([string]$Executable, [string[]]$Arguments)
    $taskOutput = & $Executable @Arguments
    $taskExitCode = $LASTEXITCODE
    if ($taskExitCode -ne 0) {
        throw ("Command failed (exit {0}): {1}" -f $taskExitCode, ($taskOutput -join "`n"))
    }
    $taskResult = ($taskOutput -join "`n") | ConvertFrom-Json
    if (-not $taskResult.ok -or $taskResult.partial) {
        throw 'Command did not return a complete successful JSON response.'
    }
    return $taskResult
}

try {
    if ($env:OS -ne 'Windows_NT') { throw 'The installer currently supports Windows only.' }
    if ($CheckOnly -and $Library) { throw '-CheckOnly uses the installed library; omit -Library.' }
    $taskConfigPath = Join-Path $env:LOCALAPPDATA 'xhs-context\config.json'
    $taskExistingConfig = $null
    if (Test-Path -LiteralPath $taskConfigPath) {
        $taskExistingConfig = Get-Content -LiteralPath $taskConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    # Keep an existing archive unless a new location was explicitly requested.
    $taskSelectedLibrary = $Library
    if (-not $taskSelectedLibrary -and $taskExistingConfig) {
        $taskSelectedLibrary = $taskExistingConfig.library
    }
    if ($taskSelectedLibrary) { $taskSelectedLibrary = [IO.Path]::GetFullPath($taskSelectedLibrary) }
    if ($env:XHS_CONTEXT_HOME -and $taskSelectedLibrary -and
        [IO.Path]::GetFullPath($env:XHS_CONTEXT_HOME) -ne [IO.Path]::GetFullPath($taskSelectedLibrary)) {
        throw 'XHS_CONTEXT_HOME points to a different library. Resolve the override before installing or checking.'
    }
    if (-not $SkillDestination) {
        $taskCodexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
        $SkillDestination = Join-Path $taskCodexRoot 'skills\xhs-context'
    }
    $SkillDestination = [IO.Path]::GetFullPath($SkillDestination)
    if (-not $CheckOnly) {
        # Capture both streams at the process boundary. PowerShell 5.1 treats
        # redirected native stderr (including uv progress) as terminating errors
        # under ErrorActionPreference=Stop, even when the command succeeds.
        $taskInstallPath = (Join-Path $PSScriptRoot 'install.ps1').Replace("'", "''")
        $taskInstallCommand = "& '$taskInstallPath' -SkillDestination '" + $SkillDestination.Replace("'", "''") + "'"
        if ($taskSelectedLibrary) {
            $taskInstallCommand += " -Library '" + $taskSelectedLibrary.Replace("'", "''") + "'"
        }
        $taskEncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding(`$false); " + $taskInstallCommand))
        $taskStart = New-Object Diagnostics.ProcessStartInfo
        $taskStart.FileName = 'powershell.exe'
        $taskStart.Arguments = '-NoProfile -NonInteractive -OutputFormat Text -ExecutionPolicy Bypass -EncodedCommand ' + $taskEncodedCommand
        $taskStart.UseShellExecute = $false
        $taskStart.CreateNoWindow = $true
        $taskStart.RedirectStandardOutput = $true
        $taskStart.RedirectStandardError = $true
        $taskStart.StandardOutputEncoding = [Text.Encoding]::UTF8
        $taskStart.StandardErrorEncoding = [Text.Encoding]::UTF8
        $taskProcess = New-Object Diagnostics.Process
        $taskProcess.StartInfo = $taskStart
        try {
            $null = $taskProcess.Start()
            $taskStdout = $taskProcess.StandardOutput.ReadToEndAsync()
            $taskStderr = $taskProcess.StandardError.ReadToEndAsync()
            $taskProcess.WaitForExit()
            [Console]::Error.Write($taskStdout.Result)
            [Console]::Error.Write($taskStderr.Result)
            if ($taskProcess.ExitCode -ne 0) {
                throw ('Installation failed (exit {0}); see stderr for details.' -f $taskProcess.ExitCode)
            }
        } finally { $taskProcess.Dispose() }
    }
    if (-not (Test-Path -LiteralPath $taskConfigPath -PathType Leaf)) {
        throw 'Installation configuration is missing. Run this script without -CheckOnly first.'
    }
    $taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $taskConfig.cli_path -or -not (Test-Path -LiteralPath $taskConfig.cli_path -PathType Leaf)) {
        throw 'The configured CLI is missing. Re-run installation in the original checkout.'
    }
    $taskSkillFile = Join-Path $SkillDestination 'SKILL.md'
    $taskSkillLauncher = Join-Path $SkillDestination 'scripts\xhs.ps1'
    if (-not (Test-Path -LiteralPath $taskSkillFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath $taskSkillLauncher -PathType Leaf)) {
        throw 'The Skill or its CLI launcher is missing. Re-run installation.'
    }
    $taskDoctor = Invoke-JsonCommand -Executable $taskConfig.cli_path -Arguments @('doctor')
    if (-not $taskDoctor.data.fts5 -or -not $taskDoctor.data.library_initialized) {
        throw 'SQLite FTS5 or the configured library is not ready.'
    }
    # Exercise the installed Skill from a different working directory.
    Push-Location -LiteralPath ([IO.Path]::GetTempPath())
    try {
        $taskStatus = Invoke-JsonCommand -Executable 'powershell.exe' -Arguments @(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $taskSkillLauncher, 'status')
    } finally { Pop-Location }
    if (-not $taskConfig.library -or
        [IO.Path]::GetFullPath($taskStatus.data.library) -ne [IO.Path]::GetFullPath($taskConfig.library)) {
        throw 'The Skill did not resolve the configured library. Check XHS_CONTEXT_HOME and config.json.'
    }
    [ordered]@{
        schema_version = 1
        ok = $true
        check_only = [bool]$CheckOnly
        state = 'local_cli_ready'
        cli = $taskConfig.cli_path
        version = $taskDoctor.data.packages.'xhs-context'
        library = $taskStatus.data.library
        skill = $taskSkillFile
        extension_dir = $taskConfig.extension_dir
        checks = @{ doctor = 'passed'; skill_launcher = 'passed'; configured_library = 'passed' }
        notes = $taskStatus.data.coverage.notes
        access_restriction = $taskStatus.data.access_restriction
        browser_connection = 'not_verified'
        agent_skill_loading = 'not_verified'
        next_steps = @('Load or reload browser-extension in Chrome/Edge if collection is needed.',
                       'Open a new Agent chat and verify it can load the Skill.',
                       'Query archived notes; acquisition requires a separate user request.')
    } | ConvertTo-Json -Depth 12 -Compress
    exit 0
} catch {
    [ordered]@{
        schema_version = 1
        ok = $false
        check_only = [bool]$CheckOnly
        error = @{ code = 'INSTALL_OR_CHECK_FAILED'; message = $_.Exception.Message }
    } | ConvertTo-Json -Depth 6 -Compress
    exit 1
}
