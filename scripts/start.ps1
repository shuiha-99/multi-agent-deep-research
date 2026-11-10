param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend/dist/index.html'))) { throw 'Build the frontend first with scripts/setup.ps1.' }
Push-Location -LiteralPath $ProjectRoot
try {
    Write-Host "Open http://127.0.0.1:$Port . Press Ctrl+C to stop."
    & $PythonPath -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $Port
} finally { Pop-Location }

