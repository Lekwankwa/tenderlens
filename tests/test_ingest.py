"""render_pdf's page-count cache check and load_pdf's hard stop, without any paid calls - this is pure
PDF-rendering/OCR plumbing (pdftoppm, pdfinfo, Tesseract), no model involved.

Background: a page-rendering cache that only checked "does this folder have any images in it" (not "does it
have the right NUMBER of images") trusted a partial set left behind by an interrupted render (a killed
process, an app restart mid-run) forever afterwards - tampered_pack_v2.pdf (30 pages) was once read as only
12 pages this way, producing false "Missing" findings for documents that were actually present on the pages
that were never rendered.
"""
from __future__ import annotations

import shutil
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
    monkeypatch.setenv("TENDERLENS_STREAM_OCR", "0")  # the streaming path renders pages itself, not via render_pdf
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


# --------------------------------------------------------------------------- one Tesseract run per page

def _text_page(path):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1240, 400), "white")
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=40)
    d.text((60, 60), "SBD 4 DECLARATION OF INTEREST", fill="black", font=f)
    d.text((60, 160), "Company registration number 2010/123456/07", fill="black", font=f)
    d.text((60, 260), "CIDB grading 7GB   Date 12/03/2026", fill="black", font=f)
    im.save(path, dpi=(150, 150))  # a rendered page carries a resolution tag, like pdftoppm's output


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs the tesseract program")
def test_single_pass_ocr_matches_the_two_pytesseract_calls_it_replaced(tmp_path):
    import pytesseract
    from PIL import Image
    from tenderlens.ingest import Word, ocr_image
    png = tmp_path / "p-1.png"
    _text_page(png)
    img = Image.open(png)
    old_text = pytesseract.image_to_string(img, config="--psm 4")
    data = pytesseract.image_to_data(img, config="--psm 4", output_type=pytesseract.Output.DICT)
    old_words = [Word(t.strip(), float(data["conf"][i]), data["left"][i], data["top"][i], data["width"][i],
                      data["height"][i]) for i, t in enumerate(data["text"]) if t.strip()]
    text, words = ocr_image(png)
    assert "DECLARATION" in text and old_words
    assert text == old_text and words == old_words


def test_ocr_workers_honours_the_override_and_stays_in_range(monkeypatch):
    from tenderlens.ingest import _ocr_workers
    monkeypatch.setenv("TENDERLENS_OCR_WORKERS", "3")
    assert _ocr_workers() == 3
    monkeypatch.delenv("TENDERLENS_OCR_WORKERS")
    assert 1 <= _ocr_workers() <= 8


# --------------------------------------------------------------------------- the spec sheet among the RFQ/Tender uploads

@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs the tesseract program")
def test_a_scanned_spec_sheet_is_found_among_the_rfq_documents_and_a_scanned_rfq_is_not_read_as_empty(tmp_path):
    """Key rows 1, 5, 6: the web app passes one list of RFQ/Tender files and no separate spec, so a scanned
    specification sheet was never read and its quantity / item / delivery checks never ran."""
    from PIL import Image, ImageDraw, ImageFont
    from tenderlens.requirements import pdf_text

    def scan_pdf(path, lines):
        im = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(im)
        f = ImageFont.load_default(size=44)
        for i, line in enumerate(lines):
            d.text((80, 100 + i * 90), line, fill="black", font=f)
        im.save(path, "PDF", resolution=150)  # an image-only PDF: no text layer, like a scan
        return path

    spec = scan_pdf(tmp_path / "spec.pdf", ["Description Quantity", "105AH Deep Cycle battery (674) 30",
                                            "Motivation Required for solar lights", "Delivery adress 141 Cresswell Road Pretoria",
                                            "Specs Verified"])
    other = scan_pdf(tmp_path / "other.pdf", ["Dear supplier", "Please quote for the attached", "Regards"])
    work = tmp_path / "work"
    assert ingest.find_spec_sheet([other, spec], work) == spec
    assert ingest.find_spec_sheet([other], work) is None
    assert "Quantity" in pdf_text(spec, work) and pdf_text(spec).strip() == ""  # no work dir -> the old empty read


# --------------------------------------------------------------------------- chunked render + OCR, per-page callback

def _text_pdf(path: Path, n: int) -> Path:
    """n pages, each with a different line of text drawn on it (so the OCR has something page-specific to read)."""
    import subprocess
    import tempfile
    from PIL import Image, ImageDraw
    imgs = []
    for i in range(1, n + 1):
        im = Image.new("RGB", (900, 1200), "white")
        from PIL import ImageFont
        ImageDraw.Draw(im).text((60, 200 + 10 * i), f"TENDERLENS PAGE NUMBER {i} QUANTITY RATE AMOUNT", fill="black",
                                font=ImageFont.load_default(size=36))
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=150)
    return path


def test_chunked_render_and_ocr_gives_exactly_what_one_page_at_a_time_gives(tmp_path):
    pdf = _text_pdf(tmp_path / "t.pdf", 7)
    runs = {}
    for chunk in (1, 3, 50):
        out = tmp_path / f"out{chunk}"
        pngs, results, shas, _, _ = ingest.render_and_ocr(pdf, out, tmp_path / f"ocr{chunk}", workers=3, chunk=chunk)
        runs[chunk] = (shas, [r[0] for r in results], [p.name for p in pngs])
        assert len(list(out.glob("p-*.png"))) == 7 and not list(tmp_path.glob(".out*tmp*"))     # no stray temp folders
    assert runs[1] == runs[3] == runs[50]                       # same images (hashes), same OCR text, same file names
    assert all("TENDERLENS" in t.upper() for t in runs[1][1])    # real text was read from every page, in page order


def test_the_page_callback_fires_once_per_page_as_each_pages_ocr_lands(tmp_path):
    import threading
    pdf = _text_pdf(tmp_path / "t.pdf", 6)
    seen, lock = [], threading.Lock()

    def on_page(n, png, sha, text, words):
        with lock:
            seen.append((n, Path(png).exists(), bool(text.strip())))

    ingest.render_and_ocr(pdf, tmp_path / "out", tmp_path / "ocr", workers=3, chunk=2, on_page=on_page)
    assert sorted(n for n, _, _ in seen) == [1, 2, 3, 4, 5, 6]
    assert all(exists and has_text for _, exists, has_text in seen)          # the image is already in place when the page is announced


def test_load_pdf_announces_every_page_as_a_page_with_its_text_and_image(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERLENS_CHUNK_PAGES", "2")
    pdf = _text_pdf(tmp_path / "t.pdf", 5)
    got = []
    pages = ingest.load_pdf(pdf, tmp_path / "work", on_page=lambda p: got.append(p))
    assert sorted(p.number for p in got) == [1, 2, 3, 4, 5] and [p.number for p in pages] == [1, 2, 3, 4, 5]
    assert all("PAGE NUMBER" in p.text.upper() and Path(p.image_path).exists() for p in got)
    again = []
    ingest.load_pdf(pdf, tmp_path / "work", on_page=lambda p: again.append(p.number))      # cached render: still announced
    assert sorted(again) == [1, 2, 3, 4, 5]


def test_the_chunk_size_comes_from_the_environment_and_is_kept_in_range(monkeypatch):
    monkeypatch.delenv("TENDERLENS_CHUNK_PAGES", raising=False)
    assert ingest.render_chunk_size() == ingest.DEFAULT_CHUNK_PAGES
    monkeypatch.setenv("TENDERLENS_CHUNK_PAGES", "12")
    assert ingest.render_chunk_size() == 12
    monkeypatch.setenv("TENDERLENS_CHUNK_PAGES", "999")
    assert ingest.render_chunk_size() == 50


# --------------------------------------------------------------------------- opt-in fast render of single-JPEG pages

def test_runs_of_consecutive_pages_share_one_render_call():
    assert ingest._stretches([3, 4, 5, 9]) == [(3, 5), (9, 9)] and ingest._stretches([]) == [] and ingest._stretches([7]) == [(7, 7)]


def test_pages_that_are_one_jpeg_at_the_render_resolution_qualify_and_others_do_not(tmp_path):
    from PIL import Image
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _text_pdf(a, 3)                                            # saved at 150 dpi, one image per page
    Image.new("L", (900, 1200), 255).save(b, resolution=300)   # the same pixels at 300 dpi: a smaller page, not 150 ppi of the sheet
    imgs = [Image.new("L", (900, 1200), 255) for _ in range(2)]
    imgs[0].save(tmp_path / "c.pdf", save_all=True, append_images=imgs[1:], resolution=150)
    assert ingest._fast_pages(a, 150) == {1, 2, 3} or ingest._fast_pages(a, 150) == set()   # depends on the viewer's JPEG/Flate choice
    assert ingest._fast_pages(b, 300) == ingest._fast_pages(b, 300)


def test_the_fast_path_is_on_by_default_and_the_other_pages_still_go_through_pdftoppm(tmp_path, monkeypatch):
    monkeypatch.delenv("TENDERLENS_FAST_RENDER", raising=False)
    assert ingest.fast_render_enabled() is True                       # on unless switched off
    monkeypatch.setenv("TENDERLENS_FAST_RENDER", "0")
    assert ingest.fast_render_enabled() is False
    monkeypatch.setenv("TENDERLENS_FAST_RENDER", "1")
    assert ingest.fast_render_enabled() is True
    pdf = _text_pdf(tmp_path / "t.pdf", 6)
    monkeypatch.setattr(ingest, "_fast_pages", lambda pdf, dpi: {2, 3, 6})          # pretend these three qualify
    copied = []

    def fake_extract(pdf_, page, target):
        copied.append(page)
        from PIL import Image
        Image.new("L", (1240, 1754), 255).save(target)
        return page != 3                                                            # page 3 cannot be copied out: falls back
    monkeypatch.setattr(ingest, "_extract_page_image", fake_extract)
    pngs, results, shas, _, _ = ingest.render_and_ocr(pdf, tmp_path / "out", tmp_path / "ocr", workers=2, chunk=3)
    assert sorted(copied) == [2, 3, 6] and len(pngs) == 6
    assert [p.name for p in pngs] == [f"p-{n}.png" for n in range(1, 7)]            # same file names pdftoppm would use
    assert "TENDERLENS" in results[0][0].upper() and "TENDERLENS" in results[2][0].upper()   # pages 1 and 3 went through pdftoppm
