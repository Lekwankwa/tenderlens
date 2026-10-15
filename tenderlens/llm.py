"""Thin Llama client.

TenderLens talks to Meta Llama through any OpenAI-compatible chat endpoint
(Llama API, Together, Groq, Fireworks, a self-hosted vLLM, Ollama on your own laptop, ...).
Nothing here is vendor specific: configure with environment variables.

Text model (reads the buyer's documents):
    TENDERLENS_LLM_BASE_URL   e.g. https://api.groq.com/openai/v1   or  http://localhost:11434/v1
    TENDERLENS_LLM_MODEL      e.g. llama-3.3-70b-versatile          or  llama3.2
    TENDERLENS_LLM_API_KEY    (not needed for Ollama)

Vision model (reads the scanned pages: ticks, handwriting, stamps). Optional: when these are not set the
vision model is the same as the text model, so one multimodal model can do both.
    TENDERLENS_VISION_BASE_URL   e.g. http://localhost:11434/v1
    TENDERLENS_VISION_MODEL      e.g. llama3.2-vision
    TENDERLENS_VISION_API_KEY

Other:
    TENDERLENS_LLM_TIMEOUT    seconds per call (default 300: a laptop CPU reading a page image is slow)
    TENDERLENS_LLM_JSON_MODE  1 (default) asks the server for strict JSON; 0 turns that off for servers that refuse it
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

_env_loaded = False


def load_env() -> None:
    """Load .env from the working directory, then the project root, if python-dotenv is installed. Real
    environment variables always win, so this is just a convenience: `export`-ing still works without it."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)


load_env()


class LLMUnavailable(RuntimeError):
    pass


class LlamaClient:
    def __init__(self, base_url=None, model=None, api_key=None, timeout=None, prefix="LLM"):
        e = os.environ.get
        self.base_url = (base_url or e(f"TENDERLENS_{prefix}_BASE_URL", "")).rstrip("/")
        self.model = model or e(f"TENDERLENS_{prefix}_MODEL", "")
        self.api_key = api_key or e(f"TENDERLENS_{prefix}_API_KEY", "")
        self.timeout = float(timeout or e("TENDERLENS_LLM_TIMEOUT", "300"))
        self.json_mode = e("TENDERLENS_LLM_JSON_MODE", "1") != "0"
        self.calls = 0
        self.errors: list[str] = []  # why calls failed, so the report can say so instead of failing silently

    @classmethod
    def for_vision(cls, text_client: "LlamaClient | None" = None) -> "LlamaClient":
        """The client that reads page images: TENDERLENS_VISION_* if set, otherwise the text model."""
        v = cls(prefix="VISION")
        if v.configured:
            return v
        t = text_client or cls()
        v.base_url, v.model, v.api_key = t.base_url, t.model, t.api_key
        return v

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    def complete_json(self, system: str, user: str, images: list[str] | None = None) -> dict:
        if not self.configured:
            raise LLMUnavailable(
                "No Llama endpoint configured. Set TENDERLENS_LLM_BASE_URL and TENDERLENS_LLM_MODEL."
            )
        content: list[dict] = [{"type": "text", "text": user}]
        for img in images or []:
            b64 = base64.b64encode(Path(img).read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content if images else user},
            ],
        }
        self.calls += 1
        try:
            if self.json_mode:
                try:
                    payload = self._post({**body, "response_format": {"type": "json_object"}})
                except urllib.error.HTTPError as err:
                    if err.code not in (400, 422):
                        raise
                    payload = self._post(body)  # this server does not accept response_format
            else:
                payload = self._post(body)
            return parse_json_loose(payload["choices"][0]["message"]["content"])
        except Exception as err:  # noqa: BLE001 - recorded, then re-raised for the caller to decide
            self.errors.append(f"{type(err).__name__}: {str(err)[:160]}")
            raise

    def ping(self) -> str:
        """One tiny call, to tell the user whether the endpoint and model are reachable. Returns '' if fine."""
        try:
            self.complete_json("Reply with JSON only.", 'Return {"ok": true}')
            return ""
        except Exception as err:  # noqa: BLE001
            return f"{type(err).__name__}: {str(err)[:200]}"


def parse_json_loose(text: str) -> dict:
    """Llama sometimes wraps JSON in prose or code fences; pull the object out."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)
