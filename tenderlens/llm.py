"""Client for Fireworks AI's OpenAI-compatible chat API - TenderLens's only model provider. One multimodal model
(Muse Spark Open) reads both the buyer's documents (text) and the scanned pages (vision).

    FIREWORKS_API_KEY    the key (required; without it the client is "not configured")
    FIREWORKS_MODEL      default accounts/fireworks/models/muse-spark-open
    FIREWORKS_API_BASE   default https://api.fireworks.ai/inference/v1 (point it at a local OpenAI-compatible
                         server, e.g. Ollama's http://localhost:11434/v1, to run without the hosted model - see OLLAMA.md)

Other:
    TENDERLENS_LLM_REASONING_EFFORT / TENDERLENS_VISION_REASONING_EFFORT   low | medium | high. Default "low" on
        Fireworks, nothing on any other endpoint. Muse Spark Open cannot switch reasoning off ("none" is rejected);
        "low" halved the buyer-requirements step (about 55 s to 27 s, identical checklist in 6 runs) and a full-page
        read (6.6 s to 3.7 s), and found 60 of the 61 testable defects in the tampered answer-key pack.
    TENDERLENS_VISION_JPEG_QUALITY   default 85. Page images are sent as JPEG of this quality when that is clearly
        smaller than the PNG (on real scanned pages about 2.3x smaller); a page where it is not (a clean
        born-digital one) is sent as the PNG, unchanged. 0 = always send the PNG. Only what is sent changes: the
        page files on disk, OCR and the local ink/signature checks still use the original PNG.
    TENDERLENS_VISION_CONCURRENCY (alias MAX_CONCURRENT_REQUESTS)    model calls in flight at once, default 25 (1-32), enforced
        across every lane of one run. TENDERLENS_TPM_LIMIT: tokens per minute the run may use (default unset: no limit).
    TENDERLENS_BATCH_OVER / TENDERLENS_BATCH_PAGES    a pack longer than BATCH_OVER pages (default 50) is rendered, read and
        sent to the model in sequential slices of BATCH_PAGES pages (default 25); inside a slice every page starts at
        once. Results are put back in page order, so the findings do not depend on the slicing.
    TENDERLENS_LARGE_PACK_PROFILE    1 (default): for such a long pack the copy of each page sent to the model is
        downscaled to 100 DPI (the render is 150) and JPEG quality 85; OCR and the local image checks keep the full
        render. 0 turns it off. TENDERLENS_LARGE_PACK_DPI / _QUALITY change the two numbers. TENDERLENS_IMAGE_PAD (default
        15) adds that many white pixels around the whole page image sent to the model.
    TENDERLENS_VISION_TIMEOUT (page reads, default 45) and TENDERLENS_LLM_TIMEOUT (the requirements call, default 90):
        seconds before one attempt is abandoned and retried (3 retries, 2 s, 4 s, 8 s apart). A slow local model needs more.
    TENDERLENS_LLM_JSON_MODE    1 (default) asks the server for strict JSON; 0 turns that off if it ever refuses it
    TENDERLENS_LLM_PRICE_IN/_OUT, TENDERLENS_VISION_PRICE_IN/_OUT
        US$ per million tokens, input/output. Unset by default - we never invent a price. Set these (see
        .env.example for the figures as listed for the model on Fireworks, at the time they were checked)
        to see an estimated cost alongside the token totals; leave them unset to see token totals only.
"""
from __future__ import annotations

import base64
import http.client
import json
import logging
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


log = logging.getLogger("tenderlens.llm")


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


FIREWORKS_BASE = "https://api.fireworks.ai/inference/v1"
FIREWORKS_MODEL = "accounts/fireworks/models/muse-spark-open"


class ModelClient:
    def __init__(self, base_url=None, model=None, api_key=None, timeout=None, prefix="LLM"):
        """prefix is "LLM" (reads the buyer's documents) or "VISION" (reads page images): only the per-role price and
        reasoning-effort settings differ - both use the same Fireworks endpoint, model and key."""
        e = os.environ.get
        # `is not None`, not `or`: an explicitly passed "" (used to force an unconfigured client, e.g.
        # --no-vision or a test that must never touch a real endpoint) must NOT fall through to the
        # defaults the way an omitted argument does - "" or default would silently ignore that override.
        self.base_url = (base_url if base_url is not None else e("FIREWORKS_API_BASE", "") or FIREWORKS_BASE).rstrip("/")
        self.model = model if model is not None else e("FIREWORKS_MODEL", "") or FIREWORKS_MODEL
        self.api_key = api_key if api_key is not None else e("FIREWORKS_API_KEY", "")
        # A call that has not answered by then is abandoned and retried (see _post). Vision reads: 45 s (measured median 15 s,
        # p90 25 s). The text (requirements) call writes about 1,300 tokens and has taken 31 to 54 s, so 45 s would fail it
        # half the time: it gets 90 s. Each has its own setting - TENDERLENS_VISION_TIMEOUT for page reads,
        # TENDERLENS_LLM_TIMEOUT for the text call - so a long text timeout in .env cannot quietly lengthen page reads.
        default_timeout = "45" if prefix == "VISION" else "90"
        self.timeout = float(timeout or e(f"TENDERLENS_{prefix}_TIMEOUT", "") or default_timeout)
        self.json_mode = e("TENDERLENS_LLM_JSON_MODE", "1") != "0"
        q = e("TENDERLENS_VISION_JPEG_QUALITY", "85").strip()
        self.jpeg_quality = max(0, min(95, int(q))) if q.isdigit() else 85
        effort = e(f"TENDERLENS_{prefix}_REASONING_EFFORT", "").strip().lower()
        if not effort and "fireworks.ai" in self.base_url:
            effort = "low"
        self.reasoning_effort = effort if effort in ("low", "medium", "high") else ""
        self.image_scale = 1.0  # < 1: page images are shrunk before being sent (see use_large_pack_profile)
        self.image_pad = 0      # white border (pixels, at the size sent) added around the page image before it is sent
        self.budget = TokenBudget.from_env()  # tokens-per-minute guard (off unless TENDERLENS_TPM_LIMIT is set)
        self.telemetry = None                  # a telemetry.RunTelemetry, set by the pipeline when it wants live counters
        self.cached_tokens = 0  # prompt tokens the provider reports as served from its prompt cache (billed at a lower rate)
        self.price_in = _num(e(f"TENDERLENS_{prefix}_PRICE_IN", ""))
        self.price_out = _num(e(f"TENDERLENS_{prefix}_PRICE_OUT", ""))
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.retries = 0  # how many times _post() actually waited and retried, for the per-check log
        self.errors: list[str] = []  # why calls failed, so the report can say so instead of failing silently
        self.call_stats: list[tuple[float, int, int]] = []  # per successful call: seconds, output tokens, of which thinking
        self._lock = threading.Lock()  # calls/prompt_tokens/completion_tokens are updated from worker threads
        # calls in flight on this client at once, across every thread pool using it (the audit runs several
        # independent vision steps side by side, each with its own pool) - so the provider never sees more
        # than TENDERLENS_VISION_CONCURRENCY at a time from one run
        self._slots = threading.BoundedSemaphore(vision_concurrency())
        # Vision reads pages one at a time across a whole pack, so a slower, longer-patience schedule costs
        # far less relative to the run than it would for the one-shot text (requirements) call.
        # three retries, waiting 2 s, 4 s, 8 s (exponential), for vision and text alike
        self.retry_waits = (2.0, 4.0, 8.0)
        self.timeouts = 0  # calls abandoned because they did not answer in time (each is then retried)

    def use_large_pack_profile(self, page_count: int, render_dpi: int = 150) -> bool:
        """For a pack longer than batch_settings()'s threshold, send a smaller copy of each page to the model
        (100 DPI, JPEG quality 85 unless set otherwise: a page's vision token count follows its pixel size, so the
        quality keeps edges clean for handwriting without adding tokens): less to upload and to read per call across hundreds of
        pages. Only what is sent changes - OCR and the local image checks use the full render. Returns whether it
        was switched on."""
        over, _ = batch_settings()
        if page_count <= over or os.environ.get("TENDERLENS_LARGE_PACK_PROFILE", "1").strip() == "0":
            return False
        dpi = _env_int("TENDERLENS_LARGE_PACK_DPI", 100, 30, render_dpi)
        self.image_scale = dpi / render_dpi
        self.image_pad = _env_int("TENDERLENS_IMAGE_PAD", 15, 0, 200)
        self.jpeg_quality = _env_int("TENDERLENS_LARGE_PACK_QUALITY", 85, 1, 95)
        return True

    @property
    def estimated_cost(self) -> float | None:
        """US$, or None if no price has been set for this endpoint - we never show a guessed number."""
        if self.price_in is None or self.price_out is None:
            return None
        return self.prompt_tokens / 1_000_000 * self.price_in + self.completion_tokens / 1_000_000 * self.price_out

    @classmethod
    def for_vision(cls, text_client: "ModelClient | None" = None) -> "ModelClient":
        """The client that reads page images: the same endpoint, model and key as the text client (one multimodal
        model does both roles), with its own per-role price and reasoning-effort settings."""
        t = text_client or cls()
        v = cls(prefix="VISION", base_url=t.base_url, model=t.model, api_key=t.api_key)
        if v.price_in is None:
            v.price_in, v.price_out = t.price_in, t.price_out
        return v

    @property
    def configured(self) -> bool:
        """An endpoint, a model, and - for the hosted Fireworks endpoint - a key. Without FIREWORKS_API_KEY the app
        comes up as "not configured", not as "configured, but every call is refused with a 401"."""
        return bool(self.base_url and self.model and (self.api_key or "fireworks.ai" not in self.base_url))

    def _post(self, body: dict, timeout: float | None = None) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        # 429 ("model busy"), every 5xx (502 and 504 included), a timeout and a dropped or reset connection are the
        # provider's or the network's state at that moment, not a bad request: each is retried up to three times with
        # a 2 s, 4 s, 8 s back-off before this call counts as failed. Anything else (a bad key, a malformed request,
        # ...) fails immediately: retrying it can't help. The call's own timeout (self.timeout) bounds each attempt, so
        # a call can take at most 4 x timeout plus 14 s of waiting and never hangs the loop that is waiting for it.
        waits = self.retry_waits
        for attempt in range(len(waits) + 1):
            try:
                with self._slots:
                    tel = self.telemetry
                    if tel is not None:
                        tel.worker_started()          # "active workers" = calls holding one of the run's request slots
                    try:
                        with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=_HTTPS_CTX) as r:
                            return json.load(r)
                    finally:
                        if tel is not None:
                            tel.worker_finished()
            except urllib.error.HTTPError as err:
                body_text = _read_error_body(err, self.api_key)
                last_err = LLMError(f"HTTP {err.code} from {self.base_url} ({self.model}): {body_text}", err.code)
                retryable = err.code == 429 or 500 <= err.code < 600
                retry_after = err.headers.get("Retry-After") if err.headers else None
            except (urllib.error.URLError, OSError, http.client.HTTPException, ssl.SSLError) as err:
                # no HTTP status at all: a timeout, a refused or reset connection, a protocol drop or a half-sent
                # response (ConnectionResetError and RemoteDisconnected arrive while the body is being read, as plain
                # OSError / HTTPException, not as URLError). Not a bad request, so the same retry as a 429.
                reason = getattr(err, "reason", err)
                if isinstance(reason, TimeoutError) or isinstance(err, TimeoutError):
                    with self._lock:
                        self.timeouts += 1
                    if self.telemetry is not None:
                        self.telemetry.add_timeout()
                    last_err = LLMError(f"timed out after {(timeout or self.timeout):.0f} s contacting {self.base_url} ({self.model})")
                else:
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
            if self.telemetry is not None:
                self.telemetry.add_retry()
            time.sleep(wait if wait is not None else waits[attempt])
        raise last_err  # unreachable - the loop above always returns or raises

    def complete_json(self, system: str, user: str, images: list[str] | None = None, timeout: float | None = None) -> dict:
        """`timeout`, if given, replaces this client's per-attempt timeout for this one call (a dense table page needs a
        long answer; a yes/no read does not)."""
        if not self.configured:
            raise LLMUnavailable("No model configured. Set FIREWORKS_API_KEY.")
        content: list[dict] = [{"type": "text", "text": user}]
        for img in images or []:
            mime, raw = encode_image(img, self.jpeg_quality, self.image_scale, self.image_pad)
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}})
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content if images else user},
            ],
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        with self._lock:
            self.calls += 1
        return self._complete(body, timeout)

    def call_summary(self) -> dict:
        """How long calls took and how many tokens they produced (p50 / p90 / max), for the run log: tells a few
        slow outliers (which hold up a whole batch of parallel calls) apart from every call being slow."""
        with self._lock:
            stats = list(self.call_stats)
        if not stats:
            return {}

        def pct(values, q):
            v = sorted(values)
            return v[min(len(v) - 1, int(q * len(v)))]

        secs, out, think = [s[0] for s in stats], [s[1] for s in stats], [s[2] for s in stats]
        return {"n": len(stats), "p50_s": round(pct(secs, .5), 1), "p90_s": round(pct(secs, .9), 1),
                "max_s": round(max(secs), 1), "p50_tok": pct(out, .5), "max_tok": max(out), "thinking_tok": sum(think)}

    def _complete(self, body: dict, timeout: float | None = None) -> dict:
        t_call = time.monotonic()
        reserved = self.budget.estimate() if self.budget.limit else None
        self.budget.acquire()               # waits (bounded) if this minute's token budget is spent; instant when no limit is set
        try:
            if self.json_mode:
                try:
                    payload = self._post({**body, "response_format": {"type": "json_object"}}, timeout)
                except LLMError as err:
                    if err.status_code not in (400, 422):
                        raise
                    payload = self._post(body, timeout)  # this server does not accept response_format
            else:
                payload = self._post(body, timeout)
            usage = payload.get("usage") or {}
            thinking = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            tokens_used = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            self.budget.record(tokens_used, reserved)
            if self.telemetry is not None:
                self.telemetry.add_tokens(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                                          int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0))
            with self._lock:
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.cached_tokens += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)
                self.call_stats.append((time.monotonic() - t_call, int(usage.get("completion_tokens") or 0), thinking))
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


_JPEG_MUST_BE_THIS_FRACTION_OF_PNG = 0.75


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    return max(low, min(high, int(raw))) if raw.isdigit() else default


def batch_settings() -> tuple[int, int]:
    """(pack length above which work is done in slices, pages per slice): TENDERLENS_BATCH_OVER (50) and
    TENDERLENS_BATCH_PAGES (25)."""
    return _env_int("TENDERLENS_BATCH_OVER", 50, 1, 100000), _env_int("TENDERLENS_BATCH_PAGES", 25, 1, 1000)


def slices(items: list, over: int | None = None, size: int | None = None) -> list[list]:
    """`items` as one slice when it is no longer than `over`, else consecutive slices of `size`."""
    d_over, d_size = batch_settings()
    over, size = over or d_over, size or d_size
    if len(items) <= over:
        return [list(items)]
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def run_rolling(tasks: list, max_workers: int, on_done=None, return_exceptions: bool = False) -> list:
    """Run zero-arg callables with at most `max_workers` in flight at any moment, starting the next the instant one
    finishes (a rolling window, not batches: one slow call, such as a 200 s retry chain, delays only itself). Results
    come back in the order of `tasks` whatever order they finish in. A task that raises never stops the others: the
    first error is re-raised once everything has finished (or, with return_exceptions, left in its result slot).
    on_done(), if given, is called once per finished task under a lock."""
    if not tasks:
        return []
    results: list = [None] * len(tasks)
    errors: list[tuple[int, BaseException]] = []
    lock = threading.Lock()

    def run(i: int) -> None:
        try:
            results[i] = tasks[i]()
        except BaseException as err:  # noqa: BLE001 - isolated: reported after the other tasks have finished
            if return_exceptions:
                results[i] = err
            else:
                with lock:
                    errors.append((i, err))
        finally:
            if on_done:
                with lock:
                    on_done()

    if max_workers <= 1 or len(tasks) <= 1:
        for i in range(len(tasks)):
            run(i)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(run, range(len(tasks))))
    if errors:
        raise sorted(errors, key=lambda t: t[0])[0][1]
    return results


def run_in_slices(tasks: list, max_workers: int, on_done=None) -> list:
    """Kept for callers that predate the rolling window: the same as run_rolling(). Long packs used to go out in barrier
    batches of 25, where each batch waited for its slowest call; slices() now only groups pages for the "slice failed"
    record (facts.record_slice_failures) and for render/OCR chunks."""
    return run_rolling(tasks, max_workers, on_done)


class TokenBudget:
    """A tokens-per-minute guard for the provider's quota: before a call, wait (a bounded time) until the tokens used in the
    last 60 s plus an estimate for this call fit the limit; after it, record what it really used. Off (no waiting, no
    bookkeeping beyond the average) when the limit is not set. TENDERLENS_TPM_LIMIT sets it."""

    def __init__(self, tokens_per_minute: int | None = None, clock=time.monotonic, sleep=time.sleep, max_wait: float = 60.0):
        self.limit = tokens_per_minute if tokens_per_minute and tokens_per_minute > 0 else None
        self._clock, self._sleep, self.max_wait = clock, sleep, max_wait
        self._used: list[tuple[float, int]] = []      # (time, tokens) within the last minute
        self._lock = threading.Lock()
        self._calls = self._total = 0

    @classmethod
    def from_env(cls) -> "TokenBudget":
        raw = os.environ.get("TENDERLENS_TPM_LIMIT", "").strip()
        return cls(int(raw) if raw.isdigit() else None)

    def estimate(self) -> int:
        """Tokens one call will probably use: the running average of finished calls (3,000 until there is one)."""
        with self._lock:
            return max(500, self._total // self._calls) if self._calls else 3000

    def acquire(self) -> float:
        """Wait until a call of the estimated size fits the limit. Returns the seconds waited (0 when off or already fits)."""
        if self.limit is None:
            return 0.0
        need, waited = self.estimate(), 0.0
        while waited < self.max_wait:
            with self._lock:
                now = self._clock()
                self._used = [(t, n) for t, n in self._used if now - t < 60.0]
                if sum(n for _, n in self._used) + need <= self.limit or not self._used:
                    self._used.append((now, need))     # reserve the estimate; record() corrects it with the real figure
                    return waited
                wait = max(0.05, 60.0 - (now - self._used[0][0]))
            wait = min(wait, self.max_wait - waited)
            self._sleep(wait)
            waited += wait
        with self._lock:
            self._used.append((self._clock(), need))
        return waited

    def record(self, tokens: int, reserved: int | None = None) -> None:
        with self._lock:
            self._calls += 1
            self._total += max(0, tokens)
            if self.limit is not None and reserved is not None:
                for i in range(len(self._used) - 1, -1, -1):          # replace the reservation with the real figure
                    if self._used[i][1] == reserved:
                        self._used[i] = (self._used[i][0], max(0, tokens))
                        break


def encode_image(path, jpeg_quality: int, scale: float = 1.0, pad: int = 0) -> tuple[str, bytes]:
    """(mime type, bytes) to send for the page image at `path`: a JPEG when that is at least a quarter smaller
    than the PNG, otherwise the PNG exactly as it is on disk (always the answer when jpeg_quality is 0, or if
    the image cannot be re-encoded). Lossy compression is never used where it would not save anything."""
    png = Path(path).read_bytes()
    if jpeg_quality <= 0:
        return "image/png", png
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            buf = io.BytesIO()
            rgb = im.convert("RGB")
            if 0 < scale < 1:
                rgb = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.LANCZOS)
            if pad > 0:  # a white margin around the whole page, so ink that runs to the scan's edge is not read as a cut-off
                framed = Image.new("RGB", (rgb.width + 2 * pad, rgb.height + 2 * pad), (255, 255, 255))
                framed.paste(rgb, (pad, pad))
                rgb = framed
            rgb.save(buf, "JPEG", quality=jpeg_quality)
        jpg = buf.getvalue()
    except Exception:  # noqa: BLE001 - sending the original is always a safe fallback
        return "image/png", png
    if scale < 1 or pad > 0 or len(jpg) < len(png) * _JPEG_MUST_BE_THIS_FRACTION_OF_PNG:  # a shrunk page is always the JPEG
        return "image/jpeg", jpg
    return "image/png", png


def vision_concurrency() -> int:
    """TENDERLENS_VISION_CONCURRENCY, default 25, clamped to [1, 32]. Measured against Fireworks with 25 identical
    full-page reads: 25 at a time finished in 18 s, 8 at a time in 53 s, with no rate-limit errors either way."""
    raw = (os.environ.get("MAX_CONCURRENT_REQUESTS") or os.environ.get("TENDERLENS_VISION_CONCURRENCY") or "25").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 25
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
    """Models sometimes wrap JSON in prose or code fences; pull the object out."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)
