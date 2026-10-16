[CmdletBinding()]
param(
    [string]$Project,
    [string]$EditorPath,
    [string]$Python = "py",
    [string]$Prompt,
    [switch]$Chat,
    [string]$Session,
    [string]$Hooks,
    [string]$ResumeTask,
    [string]$Model,
    [string]$Endpoint,
    [string]$ApiKey,
    [ValidateRange(1, 2147483647)][int]$Timeout = 60,
    [ValidateRange(1, 2147483647)][int]$ModelTimeout = 120,
    [switch]$NoInstall,
    [switch]$NoEditor,
    [switch]$EnableNativeMcp,
    [switch]$SkipValidation,
    [switch]$ReadOnly,
    [switch]$Json,
    [string]$VerifyJson,
    [ValidateRange(0, 2)][int]$MaxCorrections = 2,
    [ValidateRange(2, 30)][int]$MaxSteps = 8,
    [ValidateRange(1, 200)][int]$MaxCommands = 80,
    [ValidateRange(1, 2147483647)][int]$TaskTimeout = 300,
    [switch]$SinglePass
)

$ErrorActionPreference = "Stop"
if (-not $Project -and $PSBoundParameters.Count -eq 0) {
    Push-Location $PSScriptRoot
    try { & $Python -m unreal_harness start; $interactiveExit = $LASTEXITCODE }
    finally { Pop-Location }
    exit $interactiveExit
}
if (-not $Project) { throw "Pass -Project for advanced options, or run without arguments for interactive setup." }
if ($ResumeTask -and ($Chat -or $Prompt -or $Session -or $SinglePass -or $ReadOnly -or $VerifyJson)) {
    throw "-ResumeTask preserves saved settings and cannot be combined with -Prompt, -Session, -SinglePass, -ReadOnly or -VerifyJson"
}
if ($Session -and ((-not $Prompt -and -not $Chat) -or $SinglePass)) { throw "-Session requires -Prompt or -Chat and the task agent" }
if ($Chat -and ($Prompt -or $SinglePass -or $Json)) { throw "-Chat cannot be combined with -Prompt, -SinglePass or -Json" }

if ($Hooks -and $SinglePass) { throw "-Hooks requires the task agent" }
if ($Hooks -and (-not $Prompt -and -not $ResumeTask -and -not $Chat)) { throw "-Hooks requires -Prompt, -ResumeTask or -Chat" }
if ($Hooks) { $Hooks = (Resolve-Path $Hooks).Path }
$repo = (Resolve-Path $PSScriptRoot).Path
$projectFile = (Resolve-Path $Project).Path
if ([IO.Path]::GetExtension($projectFile).ToLowerInvariant() -ne ".uproject") {
    throw "-Project must point to a .uproject file"
}
$projectRoot = Split-Path $projectFile -Parent
if (-not $env:PYTHONPATH) {
    $env:PYTHONPATH = $repo
} else {
    $env:PYTHONPATH = "$repo;$env:PYTHONPATH"
}

function Invoke-Python {
    param([string[]]$Arguments)
    Push-Location $repo
    try {
        & $Python @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "Python command failed with exit code $exitCode"
    }
}

Write-Host "Project: $projectFile"
Write-Host "Harness: $repo"

if (-not $NoInstall) {
    $installArgs = @(
        (Join-Path $repo "scripts/install.py"),
        "--project", $projectFile,
        "--enable-plugins"
    )
    if ($EnableNativeMcp) { $installArgs += "--enable-native-mcp" }
    Write-Host "Installing harness files and required plugins..."
    Invoke-Python $installArgs
}

if (-not $SkipValidation) {
    Write-Host "Validating installation..."
    Invoke-Python @((Join-Path $repo "scripts/validate_install.py"), $projectRoot)
}

if (-not $NoEditor) {
    if (-not $EditorPath) {
        $candidates = @(
            $env:UNREAL_EDITOR,
            "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe",
            "C:\Program Files\Epic Games\UE_5.8.3\Engine\Binaries\Win64\UnrealEditor.exe"
        ) | Where-Object { $_ }
        $EditorPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $EditorPath -or -not (Test-Path $EditorPath)) {
        throw "UnrealEditor.exe not found. Pass -EditorPath or set UNREAL_EDITOR."
    }
    Write-Host "Starting Unreal Editor..."
    Start-Process -FilePath $EditorPath -ArgumentList @('"' + $projectFile + '"') | Out-Null
    Write-Host "Waiting for Unreal Editor and Python watcher (up to $Timeout seconds)..."
    Invoke-Python @("-m", "unreal_harness", "capabilities", "--project", $projectFile, "--timeout", ([string]$Timeout))
}

$cliArgs = @("-m", "unreal_harness")
if ($Prompt -or $ResumeTask -or $Chat) {
    if ($Chat) {
        $cliArgs += @("chat", "--project", $projectFile, "--timeout", ([string]$Timeout))
        if ($Session) { $cliArgs += @("--session", $Session) }
    } elseif ($ResumeTask) {
        $cliArgs += @("resume", "--project", $projectFile, "--timeout", ([string]$Timeout), $ResumeTask)
    } else {
        $cliArgs += @("ask", "--project", $projectFile, "--timeout", ([string]$Timeout), $Prompt)
        if ($Session) { $cliArgs += @("--session", $Session) }
    }
    if ($Hooks) { $cliArgs += @("--hooks", $Hooks) }
    if ($Model) { $cliArgs += @("--model", $Model) }
    if ($Endpoint) { $cliArgs += @("--endpoint", $Endpoint) }
    if ($ApiKey) { $cliArgs += @("--api-key", $ApiKey) }
    if ($ReadOnly) { $cliArgs += "--read-only" }
    if ($Json) { $cliArgs += "--json" }
    if ($VerifyJson) { $cliArgs += @("--verify-json", $VerifyJson) }
    $cliArgs += @("--max-corrections", ([string]$MaxCorrections))
    $cliArgs += @("--llm-timeout", ([string]$ModelTimeout))
    $cliArgs += @("--max-steps", ([string]$MaxSteps), "--max-commands", ([string]$MaxCommands), "--task-timeout", ([string]$TaskTimeout))
    if ($SinglePass) { $cliArgs += "--single-pass" }
} else {
    $cliArgs += @("capabilities", "--project", $projectFile, "--timeout", ([string]$Timeout))
}

Write-Host "Running harness CLI..."
Invoke-Python $cliArgs
