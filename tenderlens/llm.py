"""Thin Llama client.

TenderLens talks to its model through any OpenAI-compatible chat endpoint (Meta's Model API, OpenRouter,
DeepInfra, Llama API, Together, Groq, Fireworks, a self-hosted vLLM, Ollama on your own laptop, ...).
Nothing here is vendor specific: configure with environment variables.

MODEL_PROVIDER   "meta" (default) or "openrouter". Picks which block below is read; everything else about
                 the client (retries, JSON mode, pricing, the key-scrubbed errors) is identical either way.

Provider "meta" - Muse Spark on Meta's own Model API. One model serves both text and vision, so there is
only one set of credentials, used for both roles:
    META_API_BASE   e.g. https://api.meta.ai/v1
    MUSE_MODEL      e.g. muse-spark-1.3
    META_API_KEY

Provider "openrouter" - kept as a switchable fallback (MODEL_PROVIDER=openrouter). Separate text/vision
config, same as before:

Text model (reads the buyer's documents):
    TENDERLENS_LLM_BASE_URL   e.g. https://openrouter.ai/api/v1   or  http://localhost:11434/v1
    TENDERLENS_LLM_MODEL      e.g. meta-llama/llama-4-scout
    TENDERLENS_LLM_API_KEY

Vision model (reads the scanned pages: ticks, handwriting, stamps). Optional: when these are not set the
vision model is the same as the text model, so one multimodal model (e.g. Llama 4 Scout) can do both.
    TENDERLENS_VISION_BASE_URL   e.g. https://openrouter.ai/api/v1
    TENDERLENS_VISION_MODEL      e.g. meta-llama/llama-4-scout
    TENDERLENS_VISION_API_KEY

Other (both providers):
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
import threading
import time
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


# Shown in place of the raw error whenever a status line reports a 429 (shared provider capacity, not an
# outage of ours) - one wording, so it reads the same everywhere a model shows as unavailable.
BUSY_MESSAGE = ("The AI service is busy right now because of heavy demand on our third-party provider. "
               "Please wait a minute and try again.")


class LLMError(LLMUnavailable):
    """An HTTP error from the endpoint. str(err) includes the server's own message (not just the status
    code), with any API key scrubbed and the body cut to about 300 characters."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def is_rate_limited(e: BaseException) -> bool:
    """True for the one failure BUSY_MESSAGE should replace: the provider's shared capacity (429), not a
    real bug - a bad key, a malformed request or an actual outage should still show its own message."""
    return isinstance(e, LLMError) and (e.status_code == 429 or "429" in str(e))


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


def provider_name() -> str:
    """MODEL_PROVIDER: "meta" (default - Muse Spark on the Meta Model API) or "openrouter" (Llama 4 Scout,
    kept as a switchable fallback). Anything else is treated as "openrouter" - an unrecognised value should
    not silently disable the model entirely."""
    return "meta" if os.environ.get("MODEL_PROVIDER", "meta").strip().lower() == "meta" else "openrouter"


class LlamaClient:
    def __init__(self, base_url=None, model=None, api_key=None, timeout=None, prefix="LLM"):
        e = os.environ.get
        self.provider = provider_name()
        if self.provider == "meta":
            # One Muse Spark model serves both text and vision, so `prefix` (LLM vs VISION) doesn't split
            # the config here the way it does for OpenRouter below - same base URL, model and key either way.
            # No in-code default for base_url/model (same rule as the OpenRouter branch): a bare, unconfigured
            # checkout must come up as "not configured", not as "configured, but every call 401s" - the
            # actual default values live in .env.example, which the user copies in explicitly.
            env_base, env_model, env_key = "META_API_BASE", "MUSE_MODEL", "META_API_KEY"
        else:
            env_base, env_model, env_key = f"TENDERLENS_{prefix}_BASE_URL", f"TENDERLENS_{prefix}_MODEL", \
                f"TENDERLENS_{prefix}_API_KEY"
        # `is not None`, not `or`: an explicitly passed "" (used to force an unconfigured client, e.g.
        # --no-vision or a test that must never touch a real endpoint) must NOT fall through to the
        # environment the way an omitted argument does - "" or env() would silently ignore that override.
        self.base_url = (base_url if base_url is not None else e(env_base, "")).rstrip("/")
        self.model = model if model is not None else e(env_model, "")
        self.api_key = api_key if api_key is not None else e(env_key, "")
        self.timeout = float(timeout or e("TENDERLENS_LLM_TIMEOUT", "300"))
        self.json_mode = e("TENDERLENS_LLM_JSON_MODE", "1") != "0"
        self.price_in = _num(e(f"TENDERLENS_{prefix}_PRICE_IN", ""))
        self.price_out = _num(e(f"TENDERLENS_{prefix}_PRICE_OUT", ""))
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.retries = 0  # how many times _post() actually waited and retried, for the per-check log
        self.errors: list[str] = []  # why calls failed, so the report can say so instead of failing silently
        self._lock = threading.Lock()  # calls/prompt_tokens/completion_tokens are updated from worker threads
        # Vision reads pages one at a time across a whole pack, so a slower, longer-patience schedule costs
        # far less relative to the run than it would for the one-shot text (requirements) call.
        self.retry_waits = (2.0, 5.0, 15.0) if prefix == "VISION" else (1.0, 2.0, 4.0)

    @property
    def estimated_cost(self) -> float | None:
        """US$, or None if no price has been set for this endpoint - we never show a guessed number."""
        if self.price_in is None or self.price_out is None:
            return None
        return self.prompt_tokens / 1_000_000 * self.price_in + self.completion_tokens / 1_000_000 * self.price_out

    @classmethod
    def for_vision(cls, text_client: "LlamaClient | None" = None) -> "LlamaClient":
        """The client that reads page images. Under MODEL_PROVIDER=meta this is always the same Muse Spark
        config as the text client (one model, both roles - see __init__). Under openrouter, it's
        TENDERLENS_VISION_* if set, otherwise the text model."""
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
        # 429 ("model busy") and 5xx are the provider's shared capacity at that moment, not a real outage -
        # often gone within seconds, so retried with back-off before this call counts as failed. 504 is the
        # one 5xx excluded: it means THIS request ran too long, so an unchanged retry just times out again
        # the same way (Meta's own error-handling docs: reduce input/output size or stream instead). Anything
        # else (a bad key, a malformed request, ...) fails immediately: retrying it can't help.
        waits = self.retry_waits
        for attempt in range(len(waits) + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=_HTTPS_CTX) as r:
                    return json.load(r)
            except urllib.error.HTTPError as err:
                body_text = _read_error_body(err, self.api_key)
                last_err = LLMError(f"HTTP {err.code} from {self.base_url} ({self.model}): {body_text}", err.code)
                retryable = err.code == 429 or (500 <= err.code < 600 and err.code != 504)
                retry_after = err.headers.get("Retry-After") if err.headers else None
            except (urllib.error.URLError, TimeoutError) as err:
                # no HTTP status at all - a timeout or a connection problem reaching the endpoint, not a bad
                # request, so it gets the same retry treatment as a 429.
                reason = getattr(err, "reason", err)
                last_err = LLMError(f"{type(reason).__name__}: {reason} contacting {self.base_url} ({self.model})")
                retryable, retry_after = True, None
            if not retryable or attempt == len(waits):
                raise last_err from None
            wait = None
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = None
            with self._lock:
                self.retries += 1
            time.sleep(wait if wait is not None else waits[attempt])
        raise last_err  # unreachable - the loop above always returns or raises

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
        with self._lock:
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
            with self._lock:
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)
            return parse_json_loose(payload["choices"][0]["message"]["content"])
        except Exception as err:  # noqa: BLE001 - recorded, then re-raised for the caller to decide
            msg = str(err)[:300] if isinstance(err, LLMError) else f"{type(err).__name__}: {str(err)[:160]}"
            with self._lock:
                self.errors.append(msg)
            raise

    def ping(self) -> str:
        """One tiny call, to tell the user whether the endpoint and model are reachable. Returns '' if fine.
        _post() already retries a 429/5xx with back-off, so one call here is enough - a second, outer retry
        would just double up on the same wait."""
        try:
            self.complete_json("Reply with JSON only.", 'Return {"ok": true}')
            return ""
        except Exception as err:  # noqa: BLE001
            return f"{type(err).__name__}: {str(err)[:200]}"


def vision_concurrency() -> int:
    """TENDERLENS_VISION_CONCURRENCY, default 8, clamped to [1, 32]."""
    raw = os.environ.get("TENDERLENS_VISION_CONCURRENCY", "8").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 8
    return max(1, min(32, n))


def run_concurrent(tasks: list, max_workers: int, on_done=None) -> list:
    """Each task is a zero-arg callable (typically one model call). Runs them on a thread pool - or
    sequentially if max_workers <= 1 or there is only one task - and returns results in the SAME order as
    `tasks`, regardless of which one actually finishes first: that is ThreadPoolExecutor.map()'s own
    ordering guarantee, not something this function has to arrange. A caller that then processes results in
    that order (e.g. by page number, since `tasks` is built in page order) sees exactly what a sequential
    run would have produced - independent vision calls, not independent findings.
    on_done(), if given, is called with no arguments once per task as it actually completes (real
    completion order, which is not task order under concurrency, and is already thread-safe to call) - a
    caller tracking "N of M" across several calls to this function (e.g. several forms' worth of pages)
    keeps that N and M itself; this function only knows about its own batch."""
    if max_workers <= 1 or len(tasks) <= 1:
        out = []
        for t in tasks:
            out.append(t())
            if on_done:
                on_done()
        return out
    lock = threading.Lock()

    def wrapped(t):
        r = t()
        if on_done:
            with lock:
                on_done()
        return r

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(wrapped, tasks))


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
