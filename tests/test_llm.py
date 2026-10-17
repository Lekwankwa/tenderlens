"""The Llama client against a local mock OpenAI-compatible server: no network, no real key needed.
Covers what Step 3 of the DeepInfra prompt asked for: a failed call keeps the server's own reason (not just
the status code, and with the key scrubbed), the vision pre-check stops a run early instead of making every
call fail the same way, and token totals (and the cost estimate they drive) add up correctly.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.facts import Facts, vision_precheck
from tenderlens.llm import LLMError, LlamaClient


class Mock:
    """script: list of (status, body) or (status, body, extra_headers), answered in order; the last one
    repeats for any extra requests."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                entry = outer.script[min(len(outer.requests) - 1, len(outer.script) - 1)]
                status, resp = entry[0], entry[1]
                extra = entry[2] if len(entry) > 2 else {}
                data = json.dumps(resp).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def ok(content, prompt_tokens=10, completion_tokens=5):
    return 200, {"choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content)}}],
                 "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


@pytest.fixture
def mock():
    made = []

    def make(script):
        m = Mock(script)
        made.append(m)
        return m

    yield make
    for m in made:
        m.close()


def client(m, **kw):
    return LlamaClient(base_url=m.url, model="meta-llama/Llama-4-Scout-17B-16E-Instruct", api_key="sk-secret-abc123",
                       **kw)


# --------------------------------------------------------------------------- failure visibility (Step 3)

def test_failed_call_keeps_the_servers_own_reason_not_just_the_status(monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)  # a 500 is retried; don't wait for real
    m = Mock([(500, {"error": {"message": "the model is overloaded, try again"}})])
    c = client(m)
    with pytest.raises(LLMError) as exc:
        c.complete_json("sys", "user")
    msg = str(exc.value)
    assert "500" in msg and "overloaded" in msg  # the body, not only "HTTP Error 500: Internal Server Error"
    assert c.errors and "overloaded" in c.errors[-1]
    m.close()


def test_error_message_is_cut_to_about_300_characters_and_key_is_scrubbed():
    m = Mock([(401, {"error": "sk-secret-abc123 " + "x" * 500})])
    c = client(m)
    with pytest.raises(LLMError) as exc:
        c.complete_json("sys", "user")
    msg = str(exc.value)
    assert "sk-secret-abc123" not in msg and "<key removed>" in msg
    assert len(msg) < 400  # status/url prefix + ~300 chars of body
    m.close()


def test_vision_precheck_stops_a_failing_endpoint_before_the_full_run(tmp_path, monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)  # a 500 is retried; don't wait for real
    m = Mock([(500, {"error": {"message": "unknown model architecture"}})])
    c = client(m)
    img = tmp_path / "p1.png"
    Image.new("RGB", (100, 100), "white").save(img)

    class FakePage:
        image_path = str(img)
        number = 1

    facts = Facts()
    ok_ = vision_precheck(c, [FakePage()], facts)
    assert ok_ is False
    assert "unknown model architecture" in facts.vision_log["stopped_early"]
    assert len(m.requests) == 4  # 1 try + 3 retries (a 500 is retryable), not a 500-call flood
    m.close()


def test_vision_precheck_passes_through_when_the_model_answers(tmp_path):
    m = Mock([ok({"ok": True})])
    c = client(m)
    img = tmp_path / "p1.png"
    Image.new("RGB", (100, 100), "white").save(img)

    class FakePage:
        image_path = str(img)
        number = 1

    facts = Facts()
    assert vision_precheck(c, [FakePage()], facts) is True
    assert facts.vision_log["stopped_early"] == ""
    m.close()


def test_vision_precheck_is_a_noop_with_no_pages():
    c = LlamaClient(base_url="http://127.0.0.1:1", model="x")  # never contacted
    assert vision_precheck(c, [], Facts()) is True


# --------------------------------------------------------------------------- token totals and cost (Step 3)

def test_token_totals_add_up_across_calls():
    m = Mock([ok({"a": 1}, 100, 20), ok({"a": 2}, 50, 10)])
    c = client(m)
    c.complete_json("sys", "u1")
    c.complete_json("sys", "u2")
    assert c.calls == 2 and c.prompt_tokens == 150 and c.completion_tokens == 30
    m.close()


def test_estimated_cost_is_none_without_a_price_and_computed_once_set():
    m = Mock([ok({"a": 1}, 1_000_000, 1_000_000)])
    c = client(m)
    assert c.estimated_cost is None  # no TENDERLENS_*_PRICE_IN/_OUT set
    c.complete_json("sys", "u")
    assert c.estimated_cost is None
    c.price_in, c.price_out = 0.10, 0.30
    assert c.estimated_cost == pytest.approx(0.10 + 0.30)  # 1M tokens each way, at $/M
    m.close()


def test_vision_inherits_text_price_when_vision_has_none(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("TENDERLENS_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("TENDERLENS_LLM_BASE_URL", "https://api.deepinfra.com/v1/openai")
    monkeypatch.setenv("TENDERLENS_LLM_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
    monkeypatch.setenv("TENDERLENS_LLM_PRICE_IN", "0.10")
    monkeypatch.setenv("TENDERLENS_LLM_PRICE_OUT", "0.32")
    v = LlamaClient.for_vision(LlamaClient())
    assert v.price_in == 0.10 and v.price_out == 0.32


# --------------------------------------------------------------------------- explicit "" must force unconfigured

def test_explicit_empty_string_disables_a_client_even_with_env_vars_set(monkeypatch):
    """base_url="" (used by --no-vision and by tests that must never touch a real endpoint) must not fall
    through to a real .env/environment value the way an omitted argument correctly does."""
    monkeypatch.setenv("TENDERLENS_LLM_BASE_URL", "https://api.deepinfra.com/v1/openai")
    monkeypatch.setenv("TENDERLENS_LLM_MODEL", "meta-llama/Llama-4-Scout-17B-16E-Instruct")
    assert LlamaClient().configured  # the env vars alone do configure a plain client
    forced_off = LlamaClient(base_url="", model="")
    assert not forced_off.configured and forced_off.base_url == "" and forced_off.model == ""


# --------------------------------------------------------------------------- JSON-mode fallback still works

def test_json_mode_falls_back_when_the_server_rejects_response_format():
    m = Mock([(400, {"error": "response_format not supported"}), ok({"a": 1})])
    c = client(m)
    assert c.complete_json("sys", "u") == {"a": 1}
    assert "response_format" in m.requests[0] and "response_format" not in m.requests[1]
    m.close()


# --------------------------------------------------------------------------- ping() retries a 429 once

def test_a_429_is_retried_and_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("tenderlens.llm.time.sleep", sleeps.append)
    m = Mock([(429, {"error": {"message": "Model busy, retry later"}}), ok({"ok": True})])
    c = client(m)
    assert c.complete_json("sys", "u") == {"ok": True}
    assert len(m.requests) == 2
    assert sleeps == [1]  # no Retry-After header in this script, so the 2**attempt backoff (1s) is used
    assert c.retries == 1  # one wait-and-retry happened, for the per-check log
    m.close()


def test_a_429_that_never_clears_is_retried_3_times_then_fails(monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)
    m = Mock([(429, {"error": {"message": "Model busy, retry later"}})])  # busy every time
    c = client(m)
    with pytest.raises(LLMError) as exc:
        c.complete_json("sys", "u")
    assert "429" in str(exc.value) and "busy" in str(exc.value).lower()
    assert len(m.requests) == 4  # 1 try + 3 retries, not endless
    m.close()


def test_a_5xx_is_retried_the_same_as_a_429(monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)
    m = Mock([(503, {"error": "overloaded"}), ok({"ok": True})])
    c = client(m)
    assert c.complete_json("sys", "u") == {"ok": True}
    assert len(m.requests) == 2
    m.close()


def test_a_non_retryable_error_is_not_retried(monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)
    m = Mock([(401, {"error": "bad key"})])
    c = client(m)
    with pytest.raises(LLMError):
        c.complete_json("sys", "u")
    assert len(m.requests) == 1  # a bad key won't fix itself on retry


def test_retry_after_header_is_honoured_over_the_default_backoff(monkeypatch):
    waits = []
    monkeypatch.setattr("tenderlens.llm.time.sleep", waits.append)
    m = Mock([(429, {"error": "busy"}, {"Retry-After": "5"}), ok({"ok": True})])
    c = client(m)
    c.complete_json("sys", "u")
    assert waits == [5.0]  # the header's value, not the 1s default backoff for attempt 0
    m.close()


def test_vision_calls_use_their_own_slower_retry_schedule(monkeypatch):
    waits = []
    monkeypatch.setattr("tenderlens.llm.time.sleep", waits.append)
    m = Mock([(429, {"error": "busy"}), (429, {"error": "busy"}), (429, {"error": "busy"}), ok({"ok": True})])
    c = client(m, prefix="VISION")
    assert c.complete_json("sys", "u") == {"ok": True}
    assert waits == [2.0, 5.0, 15.0]  # not the text client's 1s/2s/4s
    m.close()


def test_a_connection_failure_with_no_http_status_is_retried_like_a_429(monkeypatch):
    """A timeout or a refused connection never reaches urllib.error.HTTPError (there's no response at all),
    so it needs its own except clause - this is what Step 2 of the failed-vision-handling fix asked for."""
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)
    c = LlamaClient(base_url="http://127.0.0.1:1", model="x", api_key="k")  # nothing listens on port 1
    with pytest.raises(LLMError) as exc:
        c.complete_json("sys", "u")
    assert "refused" in str(exc.value).lower() or "connect" in str(exc.value).lower()


def test_ping_still_works_through_the_post_level_retry(monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)
    m = Mock([(429, {"error": {"message": "Model busy, retry later"}}), ok({"ok": True})])
    c = client(m)
    assert c.ping() == ""
    m.close()
