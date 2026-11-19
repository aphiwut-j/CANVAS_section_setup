param(
    [switch]$Demo
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'The project Python environment is missing. Install the requirements into .venv first.'
}
Set-Location -LiteralPath $projectRoot
if ($Demo) {
    $env:CANVAS_DEMO_MODE = 'true'
}
& $projectPython -m app
