param([string]$Distribution = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VendorPath = Join-Path $ProjectRoot '.cache/vendor'
$TargetPath = Join-Path $VendorPath 'redis-8.10.3-linux'
$Packages = @(
    @{ Name = 'redis-server_8.10.3-1rl1~noble1_amd64.deb'; Hash = '2f531d1d8d16ee592a6fe9f18ab8d9f0565440de6b6e1a3be764f0ef876dd57d' },
    @{ Name = 'redis-tools_8.10.3-1rl1~noble1_amd64.deb'; Hash = '5b228c5f384cf3987010fd225d9f1cb5d2e15184e31d356030bf5532b74b1f85' }
)
New-Item -ItemType Directory -Force -Path $VendorPath,$TargetPath | Out-Null
$NeedsExtraction = -not (
    (Test-Path -LiteralPath (Join-Path $TargetPath 'usr/bin/redis-server')) -and
    (Test-Path -LiteralPath (Join-Path $TargetPath 'usr/bin/redis-cli')) -and
    (Test-Path -LiteralPath (Join-Path $TargetPath 'usr/lib/redis/modules/redisearch.so'))
)
Push-Location -LiteralPath $ProjectRoot
try {
    foreach ($Package in $Packages) {
        $Destination = Join-Path $VendorPath $Package.Name
        if (-not (Test-Path -LiteralPath $Destination)) {
            $Uri = 'https://packages.redis.io/deb/pool/noble/r/re/' + $Package.Name
            Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
        }
        $ActualHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($ActualHash -ne $Package.Hash) { throw ('SHA256 verification failed: ' + $Package.Name) }
        if ($NeedsExtraction) {
            & wsl.exe -d $Distribution -- dpkg-deb -x ('.cache/vendor/' + $Package.Name) '.cache/vendor/redis-8.10.3-linux'
            if ($LASTEXITCODE -ne 0) { throw 'Package extraction failed; no system package installation was performed.' }
        }
    }
    & (Join-Path $PSScriptRoot 'redis-start.ps1') -Distribution $Distribution
} finally { Pop-Location }
