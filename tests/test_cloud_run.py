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
    for k in ("META_API_BASE", "MUSE_MODEL", "META_API_KEY",
             "META_VISION_BASE", "MUSE_VISION_MODEL", "META_VISION_API_KEY"):
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


def test_privacy_caption_names_the_provider_when_an_endpoint_is_configured(monkeypatch):
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    monkeypatch.setenv("META_API_BASE", "https://api.meta.ai/v1")
    monkeypatch.setenv("MUSE_MODEL", "muse-spark-1.3")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert any("off-device, at Fireworks AI (USA)" in c.value for c in at.caption)


def test_gcs_upload_path_renders_without_a_simulated_upload(monkeypatch):
    """Cannot simulate the upload widget's own browser-side JS any more than AppTest can simulate
    st.file_uploader (see test_pilot.py) - this just checks the GCS_UPLOADS branch renders cleanly: the two
    upload_slot() calls, the size caption, and "Audit the pack" staying disabled with nothing uploaded yet."""
    from streamlit.testing.v1 import AppTest
    from tenderlens import storage
    _blank_env(monkeypatch)
    monkeypatch.setenv("GCS_BUCKET", "fake-test-bucket")
    monkeypatch.setenv("MAX_UPLOAD_MB", "100")
    storage.set_backend_for_testing(object())  # must never actually be called in this test
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        assert not at.exception
        assert any("Files up to 100 MB each" in c.value for c in at.caption)
        assert any(b.label == "Audit the pack" and b.disabled for b in at.button)
    finally:
        storage.set_backend_for_testing(None)


def test_pack_stage_button_highlights_after_a_single_click(monkeypatch):
    """Regression: the highlight used to be decided before the click was processed, so the first press was
    recorded but looked like nothing happened, and users pressed twice."""
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception

    def btn(label):
        return next(b for b in at.button if b.label == label)

    assert btn("Final pack").proto.type == "secondary"
    btn("Final pack").click().run()
    assert at.session_state["pack_stage"] == "final"
    assert btn("Final pack").proto.type == "primary"
    assert btn("Draft pack").proto.type == "secondary"
    btn("Draft pack").click().run()
    assert btn("Draft pack").proto.type == "primary"
    assert btn("Final pack").proto.type == "secondary"


def test_privacy_page_is_built_from_the_markdown_and_rebuilt_when_it_changes(tmp_path):
    import os
    from tenderlens import privacy_page
    src, out = tmp_path / "PRIVACY.md", tmp_path / "privacy.html"
    src.write_text("# Notice\n\nWe keep **nothing**.", encoding="utf-8")
    assert privacy_page.build(src, out)
    assert "<h1>Notice</h1>" in out.read_text() and "max-width: 800px" in out.read_text()
    src.write_text("# Notice\n\nChanged.", encoding="utf-8")
    os.utime(src, (out.stat().st_mtime + 5,) * 2)
    privacy_page.build(src, out)
    assert "Changed." in out.read_text()


def test_privacy_page_falls_back_to_a_placeholder_when_the_source_is_missing(tmp_path):
    from tenderlens import privacy_page
    out = tmp_path / "privacy.html"
    assert privacy_page.build(tmp_path / "nope.md", out)
    assert "temporarily unavailable" in out.read_text()


def test_a_disabled_audit_button_says_what_is_missing(monkeypatch):
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    hint = [c.value for c in at.caption if c.value.startswith("To start the audit:")]
    assert hint and "choose Draft pack or Final pack" in hint[0] and "tick the privacy box" in hint[0]


def test_the_page_never_calls_the_models_just_to_render(monkeypatch):
    """A live ping on every page load left the page blank (and the Audit button dead) while a hosted model woke up."""
    from streamlit.testing.v1 import AppTest
    from tenderlens import llm as llm_mod
    _blank_env(monkeypatch)
    monkeypatch.setenv("META_API_BASE", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MUSE_MODEL", "m")
    calls = []
    monkeypatch.setattr(llm_mod.LlamaClient, "ping", lambda self: calls.append(1) or "")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception and calls == []
