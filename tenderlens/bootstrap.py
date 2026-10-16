"""Make the pieces TenderLens depends on work together: check the programs, start Ollama, fetch the Llama
model. Nothing is installed behind your back: a missing program gets a one-line install hint."""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse

from .llm import LlamaClient, load_env

HINTS = {
    "tesseract": {"Darwin": "brew install tesseract", "Linux": "sudo apt install tesseract-ocr",
                  "Windows": "winget install UB-Mannheim.TesseractOCR"},
    "pdftoppm": {"Darwin": "brew install poppler", "Linux": "sudo apt install poppler-utils",
                 "Windows": "scoop install poppler   (or choco install poppler)"},
    "pdftotext": {"Darwin": "brew install poppler", "Linux": "sudo apt install poppler-utils",
                  "Windows": "scoop install poppler   (or choco install poppler)"},
    "ollama": {"Darwin": "brew install ollama   (or the app from ollama.com)",
               "Linux": "curl -fsSL https://ollama.com/install.sh | sh",
               "Windows": "winget install Ollama.Ollama"},
}


def hint(tool: str) -> str:
    return HINTS[tool].get(platform.system(), "see README")


def is_local(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def remote_warning(vision: LlamaClient) -> str:
    host = urlparse(vision.base_url).hostname or ""
    if vision.configured and host not in ("localhost", "127.0.0.1", "::1"):
        return (f"NOTE: page images of the bid (IDs, tax numbers, signatures, unredacted) are sent to {host}, "
                "a third party.")
    return ""


def _ollama_has(model: str) -> bool | None:
    """True/False if `ollama list` answers, None if ollama itself cannot be reached (not just this model)."""
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    tag = model.split(":")[0]
    return any(line.split()[0] == model or line.split()[0].split(":")[0] == tag
              for line in out.stdout.splitlines()[1:] if line.split())


def system_check() -> list[tuple[str, bool, str]]:
    """(item, ok, detail) for every program and endpoint the product needs."""
    load_env()
    rows = [(t, bool(shutil.which(t)), "" if shutil.which(t) else f"install: {hint(t)}")
            for t in ("tesseract", "pdftoppm", "pdftotext")]
    text = LlamaClient()
    vision = LlamaClient.for_vision(text)
    for name, c in (("text model", text), ("vision model", vision)):
        if not c.configured:
            rows.append((name, False, "not configured: copy .env.example to .env, or export TENDERLENS_* "
                        "(see OLLAMA.md)"))
            continue
        err = c.ping()
        if not err:
            rows.append((f"{name} ({c.model})", True, c.base_url))
            continue
        local_hint = ""
        if is_local(c.base_url):
            local_hint = (f"; install: {hint('ollama')}" if not shutil.which("ollama") else
                         "; start it with: ollama serve" if "refused" in err.lower() or "URLError" in err else
                         f"; try: ollama pull {c.model}" if "404" in err else "")
        rows.append((f"{name} ({c.model})", False, f"{err}{local_hint}"))
    return rows


def ensure_local_models(log=print, wait: int = 40) -> bool:
    """For local endpoints only: start `ollama serve` if nothing answers, then pull any missing model.
    Returns True once every configured local model pings successfully."""
    load_env()
    ok, started_serve, pulled = True, False, set()
    for name, c in (("text", LlamaClient()), ("vision", LlamaClient.for_vision(LlamaClient()))):
        if not c.configured or not is_local(c.base_url):
            continue
        if not shutil.which("ollama"):
            log(f"Ollama is not installed. Install it: {hint('ollama')}")
            return False
        have = _ollama_has(c.model)
        if have is None and not started_serve:
            log("Starting Ollama ...")
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            started_serve = True
            for _ in range(wait):
                time.sleep(1)
                have = _ollama_has(c.model)
                if have is not None:
                    break
        if have is False and c.model not in pulled:
            log(f"Downloading {c.model} (can be several GB, once only) ...")
            if subprocess.run(["ollama", "pull", c.model]).returncode != 0:
                log(f"Could not pull {c.model}.")
                ok = False
            pulled.add(c.model)
        err = c.ping()
        if err:
            log(f"{name} model ({c.model}) still not answering: {err}")
            ok = False
    return ok


def launch_ui(port: int = 8501) -> int:
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app.py"
    if not ensure_local_models():
        print("Continuing without a working local model; the app's System panel will show what is missing.",
              file=sys.stderr)
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(port)])
