# One-time setup for Windows (PowerShell). Poppler has no winget package; this uses scoop for it.
# After this: .\.venv\Scripts\python -m tenderlens ui
Set-Location $PSScriptRoot
winget install -e --id UB-Mannheim.TesseractOCR
winget install -e --id Ollama.Ollama
if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) { Write-Host "Install scoop (https://scoop.sh), then run: scoop install poppler"; }
else { scoop install poppler }
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "Open a NEW terminal so PATH updates apply, then run:  .\.venv\Scripts\python -m tenderlens ui"
