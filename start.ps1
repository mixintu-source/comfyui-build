[CmdletBinding()]
param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "创建虚拟环境…"
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Test-Path "web\dist\index.html")) {
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw "未找到 pnpm。请先执行：npm install -g pnpm"
    }
    Write-Host "构建前端…"
    Push-Location web
    try {
        if (-not (Test-Path "node_modules")) {
            pnpm install
        }
        pnpm build
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "ComfyUI 模型迁移管理：http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "API 文档：http://127.0.0.1:$Port/docs" -ForegroundColor DarkGray
Write-Host ""

& .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port $Port
