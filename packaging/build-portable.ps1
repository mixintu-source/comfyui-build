param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$runtime = Join-Path $root "packaging\runtime"
if (-not (Test-Path (Join-Path $runtime "python.exe"))) {
    throw "packaging/runtime is missing; prepare the embeddable Python runtime first"
}
if (-not (Test-Path "web\dist\index.html")) {
    throw "web/dist/index.html not found; run pnpm build in web/ first"
}

$releaseRoot = Join-Path $root "release"
$stage = Join-Path $releaseRoot "ComfyUI-Model-Migrator-v$Version"
$zip = Join-Path $releaseRoot "ComfyUI-Model-Migrator-v$Version-Windows-x64.zip"

Write-Host "==> stage portable release"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$files = @(
    "launcher.py", "main.py", "config_store.py", "db.py", "schemas.py",
    "settings.py", "version.py", "CHANGELOG.md"
)
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $root $file) -Destination $stage -Force
}
foreach ($dir in @("routers", "services")) {
    Copy-Item -LiteralPath (Join-Path $root $dir) -Destination $stage -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $stage "web") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $root "web\dist") -Destination (Join-Path $stage "web\dist") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\runtime") -Destination (Join-Path $stage "runtime") -Recurse -Force
$startName = (-join ([char]0x542F, [char]0x52A8)) + ".cmd"
Copy-Item -LiteralPath (Join-Path $root "packaging\PORTABLE-START.cmd") -Destination (Join-Path $stage $startName) -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\PORTABLE-START.cmd") -Destination (Join-Path $stage "Start-Portable.cmd") -Force
Copy-Item -LiteralPath (Join-Path $root "packaging\USER-GUIDE.txt") -Destination (Join-Path $stage ((-join ([char]0x4F7F, [char]0x7528, [char]0x8BF4, [char]0x660E)) + ".txt")) -Force

# Remove build-only caches and any accidental data.
Get-ChildItem -Path $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
$shippedData = Join-Path $stage "data"
if (Test-Path $shippedData) { Remove-Item -LiteralPath $shippedData -Recurse -Force }

Write-Host "==> zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
$zipped = $false
for ($attempt = 1; $attempt -le 4; $attempt++) {
    try {
        Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal -ErrorAction Stop
        $zipped = $true
        break
    }
    catch {
        if ($attempt -eq 4) { throw }
        Write-Host "zip locked, retry $attempt..."
        Start-Sleep -Seconds 4
    }
}
if (-not $zipped) { throw "zip failed" }

$zipInfo = Get-Item $zip
Write-Host ""
Write-Host "Release: $($zipInfo.FullName)"
Write-Host ("Size: {0:N1} MB" -f ($zipInfo.Length / 1MB))
Write-Host ("Folder: {0}" -f $stage)
