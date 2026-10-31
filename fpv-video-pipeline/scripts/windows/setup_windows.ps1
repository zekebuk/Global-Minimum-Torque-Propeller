# One-time Windows setup for the FPV pipeline. Run from the repo folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\windows\setup_windows.ps1 -Root D:\FPV
# Installs Python 3.11 and FFmpeg with winget (skipped if present), creates .venv, installs requirements,
# writes your SSD paths into config.toml and runs the setup check. Re-running is safe.
param([Parameter(Mandatory = $true)][string]$Root)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

function Have($cmd) { $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }

if (-not (Have "py") -or -not ((py -0p) -match "3\.11")) {
    Write-Host "Installing Python 3.11..."
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
}
if (-not (Have "ffmpeg")) {
    Write-Host "Installing FFmpeg..."
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pywinauto
.\.venv\Scripts\python.exe scripts\setup_check.py --root $Root
Write-Host "`nUse this interpreter for the pipeline AND the Resolve MCP server config:"
Write-Host (Resolve-Path .venv\Scripts\python.exe)
