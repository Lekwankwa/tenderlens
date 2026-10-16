"""Fast check / Complete check: no paid calls. Fast must make zero vision calls (not just skip the
'inspect' extras), the report must say which mode ran, and Fast's report must list what wasn't checked.
"""
from __future__ import annotations

import html as html_mod
import sys
from pathlib import Path

from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.audit import AuditResult
from tenderlens.cli import audit_pack
from tenderlens.facts import Facts
from tenderlens.llm import LlamaClient
from tenderlens.report import VISION_DEPENDENT_CHECKS, render_html
from tenderlens.requirements import Requirements


def _make_pdf(path: Path, text: str = "") -> Path:
    w = PdfWriter()
    w.add_blank_page(width=400, height=500)
    with open(path, "wb") as f:
        w.write(f)
    return path


def test_fast_check_makes_zero_vision_calls(tmp_path):
    rfq = _make_pdf(tmp_path / "rfq.pdf")
    pack = _make_pdf(tmp_path / "pack.pdf")
    fast_vision = LlamaClient(base_url="", model="")  # what app.py/cli.py pass when Fast check is chosen
    req, res, facts, pages = audit_pack(rfq, None, pack, tmp_path, None, tmp_path / "work",
                                        llm=LlamaClient(base_url="", model=""), vision=fast_vision)
    assert fast_vision.calls == 0
    assert facts.vision_log.get("calls", 0) == 0


def test_render_html_shows_complete_mode_with_no_not_checked_list():
    out = render_html(Requirements(), AuditResult(), Facts(), [], "Bidder", "RFQ-1", "rules only",
                      check_mode="complete")
    assert "Mode: Complete check" in out
    assert "Not checked in this mode" not in out


def test_render_html_shows_fast_mode_with_the_not_checked_list():
    out = render_html(Requirements(), AuditResult(), Facts(), [], "Bidder", "RFQ-1", "rules only",
                      check_mode="fast")
    assert "Mode: Fast check" in out
    assert "handwriting, ticks, stamps and names were not read" in out
    assert "Not checked in this mode" in out
    for c in VISION_DEPENDENT_CHECKS:
        assert html_mod.escape(c) in out
    assert "A clean Fast check says nothing about handwriting, ticks, stamps or names." in out


def test_complete_mode_defaults_still_work_like_before(tmp_path):
    """Complete check with no vision model configured behaves exactly as the pipeline always has: it just
    runs on rules and OCR, nothing crashes. Explicit base_url="" - never LlamaClient.for_vision() here, which
    would fall through to this machine's real TENDERLENS_VISION_* env vars and make a real call."""
    rfq = _make_pdf(tmp_path / "rfq.pdf")
    pack = _make_pdf(tmp_path / "pack.pdf")
    req, res, facts, pages = audit_pack(rfq, None, pack, tmp_path, None, tmp_path / "work",
                                        llm=LlamaClient(base_url="", model=""),
                                        vision=LlamaClient(base_url="", model=""))
    assert facts.vision_log.get("calls", 0) == 0  # no vision configured, same as always
