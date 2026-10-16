"""render_pdf's page-count cache check and load_pdf's hard stop, without any paid calls - this is pure
PDF-rendering/OCR plumbing (pdftoppm, pdfinfo, Tesseract), no model involved.

Background: a page-rendering cache that only checked "does this folder have any images in it" (not "does it
have the right NUMBER of images") trusted a partial set left behind by an interrupted render (a killed
process, an app restart mid-run) forever afterwards - tampered_pack_v2.pdf (30 pages) was once read as only
12 pages this way, producing false "Missing" findings for documents that were actually present on the pages
that were never rendered.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import ingest


def _make_pdf(path: Path, n_pages: int) -> Path:
    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=200, height=260)
    with open(path, "wb") as f:
        w.write(f)
    return path


def test_render_pdf_rerenders_when_the_cached_image_count_is_short(tmp_path):
    pdf = _make_pdf(tmp_path / "p.pdf", 3)
    outdir = tmp_path / "out"
    pngs = ingest.render_pdf(pdf, outdir)
    assert len(pngs) == 3

    # simulate the exact failure mode: an interrupted earlier render left fewer, individually-valid images
    pngs[-1].unlink()
    assert len(sorted(outdir.glob("p-*.png"))) == 2

    pngs_again = ingest.render_pdf(pdf, outdir)
    assert len(pngs_again) == 3  # re-rendered, not trusted as "already done"


def test_render_pdf_trusts_a_complete_cache_without_rerendering(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "p.pdf", 2)
    outdir = tmp_path / "out"
    ingest.render_pdf(pdf, outdir)

    real_run = ingest.subprocess.run

    def guarded(cmd, *a, **k):
        if cmd[0] == "pdftoppm":
            raise AssertionError("pdftoppm should not run again for a cache that already matches the page count")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(ingest.subprocess, "run", guarded)
    pngs = ingest.render_pdf(pdf, outdir)
    assert len(pngs) == 2


def test_a_failed_render_leaves_no_partial_set_at_the_real_location(tmp_path, monkeypatch):
    """The temp-folder-then-move design: if the page count pdfinfo reports can't be matched, outdir must
    end up with nothing in it (not a short, trusted-looking set), and the temp folder must not linger."""
    pdf = _make_pdf(tmp_path / "p.pdf", 2)
    outdir = tmp_path / "out"
    monkeypatch.setattr(ingest, "pdf_page_count", lambda p: 5)  # a count pdftoppm's real 2 pages can't meet

    with pytest.raises(RuntimeError, match="pdftoppm rendered"):
        ingest.render_pdf(pdf, outdir)

    assert list(outdir.glob("p-*.png")) == []
    assert list(outdir.parent.glob(f".{outdir.name}.tmp-*")) == []


def test_load_pdf_stops_with_a_clear_message_if_pages_read_dont_match_the_pdf(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "p.pdf", 3)
    # bypass render_pdf's own protection to exercise load_pdf's independent, explicit check
    monkeypatch.setattr(ingest, "render_pdf", lambda p, d: [Path("fake1.png"), Path("fake2.png")])
    with pytest.raises(RuntimeError, match=r"read 2 page\(s\) but the PDF has 3"):
        ingest.load_pdf(pdf, tmp_path / "work")


def test_load_pdf_succeeds_when_the_count_matches(tmp_path):
    pdf = _make_pdf(tmp_path / "p.pdf", 2)
    pages = ingest.load_pdf(pdf, tmp_path / "work")
    assert len(pages) == 2 and [p.number for p in pages] == [1, 2]


def test_pdf_page_count_reads_pdfinfo(tmp_path):
    pdf = _make_pdf(tmp_path / "pc_check.pdf", 4)
    assert ingest.pdf_page_count(pdf) == 4


# --------------------------------------------------------------------------- RFQ document classification

def test_classify_recognises_an_rfq_covering_letter():
    text = ("Request for Procurement of Deep Cycle Battery\nClosing date: 2026/01/15\n"
            "Quotations which are not received from this email address will be disqualified.")
    assert ingest.classify_rfq_document(text) == "email"


def test_classify_recognises_a_specification_sheet():
    text = "Description             Quantity\n105AH Deep Cycle battery      30\n\nMotivation\nRequired for site lighting."
    assert ingest.classify_rfq_document(text) == "spec"


def test_classify_returns_none_when_unclear():
    assert ingest.classify_rfq_document("Dear Sir, please find attached our requirements. Regards.") is None


def test_classify_returns_none_when_both_signals_are_present():
    text = "Closing date: 2026/01/15\nDescription             Quantity\nSPECS VERIFIED"
    assert ingest.classify_rfq_document(text) is None


# --------------------------------------------------------------------------- page count / time estimate

def test_page_count_estimate_shows_only_the_count_when_the_rate_is_unset(monkeypatch):
    monkeypatch.delenv("TENDERLENS_SECONDS_PER_PAGE_FAST", raising=False)
    out = ingest.page_count_estimate(12, "fast")
    assert out == "12 page(s)." and "minute" not in out  # no number is ever invented


def test_page_count_estimate_shows_minutes_when_the_rate_is_set(monkeypatch):
    monkeypatch.setenv("TENDERLENS_SECONDS_PER_PAGE_COMPLETE", "30")  # 30s/page
    out = ingest.page_count_estimate(12, "complete")
    assert "12 page(s)" in out and "About 6 minute(s)" in out  # 12 * 30s / 60 = 6


def test_page_count_estimate_ignores_a_junk_rate_value(monkeypatch):
    monkeypatch.setenv("TENDERLENS_SECONDS_PER_PAGE_FAST", "not-a-number")
    out = ingest.page_count_estimate(5, "fast")
    assert out == "5 page(s)."
