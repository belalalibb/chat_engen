# One command on Windows (PowerShell 7+): venv + deps + UI build + serve.
# Usage: pwsh scripts/dev.ps1 [-Port 8080]
param([int]$Port = 8080)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -q -e ".[dev]"
if (Get-Command npm -ErrorAction SilentlyContinue) {
  Push-Location ui_standalone
  npm install --no-audit --no-fund --loglevel=error
  npx vite build
  Pop-Location
} else { Write-Host "npm not found: API-only mode (no UI)" }
$env:PORT = "$Port"
Write-Host "chat_engine -> http://localhost:$Port  (UI at /, OpenAPI at /openapi.json)"
& .venv\Scripts\python.exe -m chat_engine serve
