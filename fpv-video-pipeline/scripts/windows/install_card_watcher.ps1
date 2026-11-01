# Start the card watcher automatically at login (no console window). Run once from the repo folder:
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_card_watcher.ps1
# Remove:   schtasks /Delete /TN "FPV Card Watcher" /F
# Test:     .venv\Scripts\python scripts\card_watcher.py --once --dry-run
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pyw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { throw "No .venv found: run scripts\windows\setup_windows.ps1 first" }
$script = Join-Path $repo "scripts\card_watcher.py"
schtasks /Create /F /SC ONLOGON /RL LIMITED /TN "FPV Card Watcher" /TR "`"$pyw`" `"$script`""
schtasks /Run /TN "FPV Card Watcher"
Write-Host "Card watcher installed and running. Plug in a card: the overnight run starts by itself."
Write-Host "Log: <library>\..\Logs\card_watcher.log"
