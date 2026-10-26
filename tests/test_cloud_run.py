"""Cloud Run packaging: the upload-size message and the unconditional (not just pilot) temp-file purge.
Runs the real app.py via AppTest - everything that doesn't need a simulated file_uploader (a documented
AppTest limitation; see test_pilot.py). No network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def _blank_env(monkeypatch):
    # app.py's sidebar calls system_check(), which pings whatever endpoint is configured - blank it so this
    # never makes a real network call regardless of what's in .env.
    for k in ("TENDERLENS_LLM_BASE_URL", "TENDERLENS_LLM_MODEL", "TENDERLENS_LLM_API_KEY",
             "TENDERLENS_VISION_BASE_URL", "TENDERLENS_VISION_MODEL", "TENDERLENS_VISION_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_upload_size_message_shows_the_configured_limit(monkeypatch):
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    monkeypatch.setenv("TENDERLENS_MAX_UPLOAD_MB", "30")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert any("accepts packs up to 30 MB" in c.value for c in at.caption)
    assert any("compress the PDF or send a shorter pack" in c.value for c in at.caption)


def test_upload_size_message_is_absent_when_the_env_var_is_unset(monkeypatch):
    """The existing Streamlit Community Cloud deployment (no TENDERLENS_MAX_UPLOAD_MB set) must not change -
    this message is opt-in, for the Cloud Run pilot specifically."""
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    monkeypatch.delenv("TENDERLENS_MAX_UPLOAD_MB", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert not any("accepts packs up to" in c.value for c in at.caption)


def test_privacy_caption_names_openrouter_when_that_s_the_configured_endpoint(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")  # TENDERLENS_LLM_* is the openrouter-provider config
    monkeypatch.setenv("TENDERLENS_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TENDERLENS_LLM_MODEL", "meta-llama/llama-4-scout")
    monkeypatch.delenv("TENDERLENS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TENDERLENS_VISION_BASE_URL", raising=False)
    monkeypatch.delenv("TENDERLENS_VISION_MODEL", raising=False)
    monkeypatch.delenv("TENDERLENS_VISION_API_KEY", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert any("off-device, at OpenRouter (USA)" in c.value for c in at.caption)
