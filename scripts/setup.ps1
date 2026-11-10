param([switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw 'Install Python 3.11-3.13 and add it to PATH first.' }
    & $PythonCommand.Source -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'This project requires Python 3.11-3.13.' }
    if (-not $SkipFrontend -and -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js and npm first.' }
    New-Item -ItemType Directory -Force -Path '.cache/tmp' | Out-Null
    $env:TMP = Join-Path $ProjectRoot '.cache/tmp'
    $env:TEMP = $env:TMP
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv sync --locked --python $PythonCommand.Source --cache-dir '.cache/uv' --link-mode copy --group dev
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    } else {
        & $PythonCommand.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
        & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    }
    if (-not $SkipFrontend) {
        Push-Location -LiteralPath 'frontend'
        try {
            npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    Write-Host 'Ready. Run: powershell -ExecutionPolicy Bypass -File scripts/start.ps1'
} finally { Pop-Location }

