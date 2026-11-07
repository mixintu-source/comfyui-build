param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "venv not found at .venv; create it and install requirements.txt"
}

$runtime = Join-Path $root "packaging\runtime"
$libs = Join-Path $root "packaging\py-libs"
$embedZip = Join-Path $root "packaging\python-embed.zip"

if ($Force) {
    if (Test-Path $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
    if (Test-Path $libs) { Remove-Item -LiteralPath $libs -Recurse -Force }
}

if (-not (Test-Path (Join-Path $runtime "python.exe"))) {
    if (-not (Test-Path $embedZip)) {
        Write-Host "==> download Python embeddable 3.13.5"
        Invoke-WebRequest `
            -Uri "https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-amd64.zip" `
            -OutFile $embedZip
    }
    Write-Host "==> extract Python runtime"
    if (Test-Path $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
    Expand-Archive -LiteralPath $embedZip -DestinationPath $runtime -Force
}

if (-not (Test-Path $libs)) {
    Write-Host "==> install runtime dependencies"
    & $python -m pip install --target $libs -r requirements.txt --no-cache-dir --upgrade
    if ($LASTEXITCODE -ne 0) { throw "pip install --target failed" }
}

Write-Host "==> configure embeddable path"
$site = Join-Path $runtime "Lib\site-packages"
New-Item -ItemType Directory -Path $site -Force | Out-Null
Copy-Item -Path (Join-Path $libs "*") -Destination $site -Recurse -Force
$pth = Join-Path $runtime "python313._pth"
@("python313.zip", ".", "Lib\site-packages", "import site") |
    Set-Content -LiteralPath $pth -Encoding ASCII

& (Join-Path $runtime "python.exe") -c "import fastapi, uvicorn, pydantic, multipart; print('runtime ready')"
if ($LASTEXITCODE -ne 0) { throw "runtime self-check failed" }
