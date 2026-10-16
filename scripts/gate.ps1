# Release gate on Windows (mirrors scripts/gate.sh). Usage: pwsh scripts/gate.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $py -m ruff check chat_engine tests sdk/python examples
& $py -m ruff format --check chat_engine tests sdk/python examples
$upstream = & $py -c "import core,os;print(os.path.dirname(os.path.dirname(core.__file__)))"
$env:MYPYPATH = $upstream
& $py -m mypy chat_engine
lint-imports
& $py scripts/secret_scan.py
& $py scripts/ui_no_copy_guard.py
& $py -m pytest -q -p no:warnings
Write-Host "GATE GREEN"
