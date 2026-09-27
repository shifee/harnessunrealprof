[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Project,
    [string]$EditorPath,
    [string]$Python = "py",
    [string]$Prompt,
    [string]$Model,
    [string]$Endpoint,
    [string]$ApiKey,
    [int]$Timeout = 60,
    [switch]$NoInstall,
    [switch]$NoEditor,
    [switch]$EnableNativeMcp,
    [switch]$SkipValidation
)

$ErrorActionPreference = "Stop"
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
    Start-Process -FilePath $EditorPath -ArgumentList @($projectFile) | Out-Null
    Write-Host "Waiting $Timeout seconds for Unreal Editor and Python watcher..."
    Start-Sleep -Seconds $Timeout
}

$cliArgs = @("-m", "unreal_harness")
if ($Prompt) {
    $cliArgs += @("ask", "--project", $projectFile, "--timeout", ([string]$Timeout), $Prompt)
    if ($Model) { $cliArgs += @("--model", $Model) }
    if ($Endpoint) { $cliArgs += @("--endpoint", $Endpoint) }
    if ($ApiKey) { $cliArgs += @("--api-key", $ApiKey) }
} else {
    $cliArgs += @("capabilities", "--project", $projectFile, "--timeout", ([string]$Timeout))
}

Write-Host "Running harness CLI..."
Invoke-Python $cliArgs
