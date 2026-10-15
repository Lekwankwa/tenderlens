#!/usr/bin/env bash
# One-time setup for macOS and Linux. Installs the programs TenderLens needs, the Python packages and the
# Llama model(s). After this: .venv/bin/python -m tenderlens ui
set -e
cd "$(dirname "$0")"
if [ "$(uname)" = "Darwin" ]; then
  command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
  brew install tesseract poppler ollama
else
  sudo apt install -y tesseract-ocr poppler-utils python3-venv
  command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
fi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
[ -f .env ] || cp .env.example .env
.venv/bin/python -m tenderlens check-llm || true
echo "Start the product with:  .venv/bin/python -m tenderlens ui"
