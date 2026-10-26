"""The pre-upload privacy notice and consent checkbox. No network, no paid calls."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

from tenderlens import privacy  # noqa: E402


def test_consent_required_blocks_the_run_without_the_checkbox():
    assert privacy.consent_required(ready=True, consent=False) is False


def test_consent_required_allows_the_run_once_checked():
    assert privacy.consent_required(ready=True, consent=True) is True


def test_consent_required_still_respects_other_readiness_conditions():
    """Checking the box alone must not override missing files or a bad pilot code."""
    assert privacy.consent_required(ready=False, consent=True) is False


def test_privacy_md_exists_and_covers_the_required_points():
    text = (ROOT / "PRIVACY.md").read_text()
    for marker in ("Lekwankwa Corporation", "Meta", "api.meta.ai", "africa-south1", "Streamlit Community Cloud",
                   "[CONFIRM", "[ADD when used]", "handwritten", "redaction".title(), "outside", "South Africa"):
        assert marker.lower() in text.lower(), f"PRIVACY.md is missing: {marker!r}"


def test_in_app_notice_names_meta_and_points_to_the_full_notice():
    notice = privacy.notice_short()
    assert "meta" in notice.lower() and "api.meta.ai" in notice
    assert "PRIVACY.md" in notice


# --------------------------------------------------------------------------- the real Streamlit app

def _blank_env(monkeypatch):
    for k in ("META_API_BASE", "MUSE_MODEL", "META_API_KEY",
             "META_VISION_BASE", "MUSE_VISION_MODEL", "META_VISION_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_app_shows_the_consent_checkbox_unchecked_by_default(monkeypatch):
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    monkeypatch.delenv("TENDERLENS_PILOT", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    boxes = [c for c in at.checkbox if c.label == privacy.CONSENT_LABEL]
    assert len(boxes) == 1 and boxes[0].value is False
    # disabled with no files regardless of the checkbox - this just confirms the widget renders and the
    # button is never force-enabled by it alone (file_uploader itself can't be driven from AppTest)
    assert any(b.label == "Audit the pack" and b.disabled for b in at.button)


def test_app_audit_button_stays_disabled_when_consent_is_checked_but_no_files_are_uploaded(monkeypatch):
    from streamlit.testing.v1 import AppTest
    _blank_env(monkeypatch)
    monkeypatch.delenv("TENDERLENS_PILOT", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.checkbox(key="privacy_consent").set_value(True).run()
    assert not at.exception
    assert any(b.label == "Audit the pack" and b.disabled for b in at.button)
