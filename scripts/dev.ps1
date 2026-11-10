$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend/node_modules'))) { throw 'Install frontend dependencies with scripts/setup.ps1 first.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js and npm first.' }
$PortProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8000)
try { $PortProbe.Start() } catch { throw 'Port 8000 is already in use. Stop the existing service before running scripts/dev.ps1.' } finally { $PortProbe.Stop() }
$BackendPath = Join-Path $ProjectRoot 'backend'
$LogPath = Join-Path $ProjectRoot 'data'
New-Item -ItemType Directory -Force -Path $LogPath | Out-Null
$Server = Start-Process -FilePath $PythonPath -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--app-dir', ('"' + $BackendPath + '"'), '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogPath 'dev-api.log') -RedirectStandardError (Join-Path $LogPath 'dev-api-error.log')
Push-Location -LiteralPath (Join-Path $ProjectRoot 'frontend')
try {
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        if ($Server.HasExited) { throw "Backend startup failed. See data/dev-api-error.log." }
        try {
            $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
            if ($Health.status -eq 'ok' -and $Health.features.tools -eq 9) { $Ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $Ready) { throw 'Backend did not become ready. See data/dev-api-error.log.' }
    npm.cmd run dev -- --port 5173 --strictPort
    if ($LASTEXITCODE -ne 0) { throw 'Frontend startup failed; port 5173 may already be in use.' }
} finally {
    Pop-Location
    if (-not $Server.HasExited) { Stop-Process -Id $Server.Id }
}
