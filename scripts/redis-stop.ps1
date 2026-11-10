param([string]$Distribution = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    & wsl.exe -d $Distribution -- bash scripts/redis-local.sh stop
    if ($LASTEXITCODE -ne 0) { throw 'Project Redis was not stopped. Only the owned instance may be stopped.' }
} finally { Pop-Location }
