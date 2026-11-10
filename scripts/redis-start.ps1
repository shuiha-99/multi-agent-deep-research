param([string]$Distribution = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    & wsl.exe -d $Distribution -- bash scripts/redis-local.sh start
    if ($LASTEXITCODE -ne 0) { throw 'Project Redis startup failed. See data/redis8/redis.log.' }
} finally { Pop-Location }
