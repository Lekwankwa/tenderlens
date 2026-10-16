"""Thin Llama client.

TenderLens talks to Meta Llama through any OpenAI-compatible chat endpoint
(DeepInfra, Llama API, Together, Groq, Fireworks, a self-hosted vLLM, Ollama on your own laptop, ...).
Nothing here is vendor specific: configure with environment variables.

Text model (reads the buyer's documents):
    TENDERLENS_LLM_BASE_URL   e.g. https://api.deepinfra.com/v1/openai   or  http://localhost:11434/v1
    TENDERLENS_LLM_MODEL      e.g. meta-llama/Llama-4-Scout-17B-16E-Instruct
    TENDERLENS_LLM_API_KEY

Vision model (reads the scanned pages: ticks, handwriting, stamps). Optional: when these are not set the
vision model is the same as the text model, so one multimodal model (e.g. Llama 4 Scout) can do both -
that is the default setup in .env.example: one model, same base URL and key, for both roles.
    TENDERLENS_VISION_BASE_URL   e.g. https://api.deepinfra.com/v1/openai
    TENDERLENS_VISION_MODEL      e.g. meta-llama/Llama-4-Scout-17B-16E-Instruct
    TENDERLENS_VISION_API_KEY

Other:
    TENDERLENS_LLM_TIMEOUT      seconds per call (default 300: a laptop CPU reading a page image is slow)
    TENDERLENS_LLM_JSON_MODE    1 (default) asks the server for strict JSON; 0 turns that off for servers that refuse it
    TENDERLENS_LLM_PRICE_IN/_OUT, TENDERLENS_VISION_PRICE_IN/_OUT
        US$ per million tokens, input/output. Unset by default - we never invent a price. Set these (see
        .env.example for the figures as published on the provider's own pricing page, at the time they were
        checked) to see an estimated cost alongside the token totals; leave them unset to see token totals only.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path


def _https_context() -> ssl.SSLContext | None:
    """certifi's CA bundle, if the package is installed, so a laptop whose Python never had its root
    certificates set up (a known gap in the python.org macOS installer - it ships without them until
    'Install Certificates.command' is run) can still make HTTPS calls to any hosted endpoint. Returns None
    (use urllib's normal default) if certifi isn't available."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


_HTTPS_CTX = _https_context()

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


class LLMError(LLMUnavailable):
    """An HTTP error from the endpoint. str(err) includes the server's own message (not just the status
    code), with any API key scrubbed and the body cut to about 300 characters."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _num(s: str) -> float | None:
    s = (s or "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _scrub_key(text: str, key: str) -> str:
    return text.replace(key, "<key removed>") if key else text


def _read_error_body(err: urllib.error.HTTPError, api_key: str) -> str:
    try:
        raw = err.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    raw = _scrub_key(raw, api_key).strip()
    raw = re.sub(r"\s+", " ", raw)[:300]
    return raw or err.reason or ""


class LlamaClient:
    def __init__(self, base_url=None, model=None, api_key=None, timeout=None, prefix="LLM"):
        e = os.environ.get
        # `is not None`, not `or`: an explicitly passed "" (used to force an unconfigured client, e.g.
        # --no-vision or a test that must never touch a real endpoint) must NOT fall through to the
        # environment the way an omitted argument does - "" or env() would silently ignore that override.
        self.base_url = (base_url if base_url is not None else e(f"TENDERLENS_{prefix}_BASE_URL", "")).rstrip("/")
        self.model = model if model is not None else e(f"TENDERLENS_{prefix}_MODEL", "")
        self.api_key = api_key if api_key is not None else e(f"TENDERLENS_{prefix}_API_KEY", "")
        self.timeout = float(timeout or e("TENDERLENS_LLM_TIMEOUT", "300"))
        self.json_mode = e("TENDERLENS_LLM_JSON_MODE", "1") != "0"
        self.price_in = _num(e(f"TENDERLENS_{prefix}_PRICE_IN", ""))
        self.price_out = _num(e(f"TENDERLENS_{prefix}_PRICE_OUT", ""))
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.errors: list[str] = []  # why calls failed, so the report can say so instead of failing silently

    @property
    def estimated_cost(self) -> float | None:
        """US$, or None if no price has been set for this endpoint - we never show a guessed number."""
        if self.price_in is None or self.price_out is None:
            return None
        return self.prompt_tokens / 1_000_000 * self.price_in + self.completion_tokens / 1_000_000 * self.price_out

    @classmethod
    def for_vision(cls, text_client: "LlamaClient | None" = None) -> "LlamaClient":
        """The client that reads page images: TENDERLENS_VISION_* if set, otherwise the text model."""
        v = cls(prefix="VISION")
        if v.configured:
            return v
        t = text_client or cls()
        v.base_url, v.model, v.api_key = t.base_url, t.model, t.api_key
        if v.price_in is None:
            v.price_in, v.price_out = t.price_in, t.price_out
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
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_HTTPS_CTX) as r:
                return json.load(r)
        except urllib.error.HTTPError as err:
            body_text = _read_error_body(err, self.api_key)
            raise LLMError(f"HTTP {err.code} from {self.base_url} ({self.model}): {body_text}", err.code) from None

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
                except LLMError as err:
                    if err.status_code not in (400, 422):
                        raise
                    payload = self._post(body)  # this server does not accept response_format
            else:
                payload = self._post(body)
            usage = payload.get("usage") or {}
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
            return parse_json_loose(payload["choices"][0]["message"]["content"])
        except Exception as err:  # noqa: BLE001 - recorded, then re-raised for the caller to decide
            self.errors.append(str(err)[:300] if isinstance(err, LLMError) else f"{type(err).__name__}: {str(err)[:160]}")
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
