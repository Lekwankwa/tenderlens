"""PDF -> page images -> OCR (with word boxes and confidences) -> page classification."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytesseract
from PIL import Image

DPI = 150


@dataclass
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int

    @property
    def box(self):
        return (self.left, self.top, self.left + self.width, self.top + self.height)


@dataclass
class Page:
    number: int  # 1-based page in the source PDF
    image_path: str
    text: str = ""
    words: list[Word] = field(default_factory=list)
    doc_type: str = "unknown"
    sha: str = ""  # sha1 of the rendered page image: identifies the page whatever its position in the pack

    @property
    def mean_conf(self) -> float:
        ws = [w.conf for w in self.words if w.conf >= 0]
        return sum(ws) / len(ws) if ws else 0.0


def _usable_png(png: Path) -> bool:
    """False for a page image left behind by a pdftoppm run that was interrupted partway (a killed
    process, an app restart mid-render): present on disk but not a complete, openable PNG."""
    try:
        with Image.open(png) as im:
            im.load()
        return True
    except Exception:
        return False


def pdf_page_count(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], check=True, capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s*(\d+)", out, re.M)
    if not m:
        raise RuntimeError(f"pdfinfo did not report a page count for {pdf}")
    return int(m.group(1))


def page_count_estimate(n_pages: int, check_mode: str) -> str:
    """The page count, shown before a run starts (fast or complete). With
    TENDERLENS_SECONDS_PER_PAGE_FAST/_COMPLETE set, also "About N minutes..."; unset (the default), just the
    count - no number is ever invented."""
    raw = os.environ.get(f"TENDERLENS_SECONDS_PER_PAGE_{check_mode.upper()}", "").strip()
    try:
        secs_per_page = float(raw) if raw else None
    except ValueError:
        secs_per_page = None
    if secs_per_page is None:
        return f"{n_pages} page(s)."
    minutes = max(1, round(secs_per_page * n_pages / 60))
    return (f"{n_pages} page(s). About {minutes} minute(s) for {n_pages} pages. Larger packs take longer, "
           "roughly in proportion. This is an estimate, not a promise.")


def render_pdf(pdf: Path, outdir: Path, dpi: int = DPI) -> list[Path]:
    """Page images for `pdf`, cached under outdir by the PDF's own content hash (the caller picks outdir's
    name for that). The cache is trusted only when it holds exactly as many usable images as the PDF has
    pages - not just "the folder is non-empty" - because a render that was interrupted (a killed process, an
    app restart mid-run) can leave a smaller, individually-valid set behind that would otherwise look
    complete forever and quietly shortchange every future read of this same file.
    A fresh render goes into a temporary sibling folder first and is moved into place only once every page
    came out, so an interruption during THIS render leaves an orphaned temp folder, never a partial result
    at outdir for the next call to wrongly trust."""
    outdir.mkdir(parents=True, exist_ok=True)
    want = pdf_page_count(pdf)
    existing = sorted(outdir.glob("p-*.png"))
    if existing and len(existing) == want and all(_usable_png(p) for p in existing):
        return existing
    for p in existing:
        p.unlink()
    tmp = outdir.parent / f".{outdir.name}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(tmp / "p")], check=True)
        rendered = sorted(tmp.glob("p-*.png"))
        if len(rendered) != want or not all(_usable_png(p) for p in rendered):
            raise RuntimeError(
                f"pdftoppm rendered {len(rendered)} usable page(s) from {pdf.name}, but pdfinfo reports {want}.")
        for p in rendered:
            shutil.move(str(p), str(outdir / p.name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sorted(outdir.glob("p-*.png"))


def sha1_file(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def ocr_image(png: Path, cache: Path | None = None) -> tuple[str, list[Word]]:
    """OCR one page image. The cache is keyed by the image's content hash, so a page that appears
    unchanged in a modified pack is never read twice (and always reads the same way)."""
    if cache and cache.exists():
        d = json.loads(cache.read_text())
        return d["text"], [Word(**w) for w in d["words"]]
    img = Image.open(png)
    text = pytesseract.image_to_string(img, config="--psm 4")
    data = pytesseract.image_to_data(img, config="--psm 4", output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(data["text"]):
        t = t.strip()
        if not t:
            continue
        words.append(
            Word(t, float(data["conf"][i]), data["left"][i], data["top"][i], data["width"][i], data["height"][i])
        )
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"text": text, "words": [w.__dict__ for w in words]}))
    return text, words


def _ocr_workers() -> int:
    raw = os.environ.get("TENDERLENS_OCR_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 2) - 1)


def load_pdf(pdf: Path, workdir: Path, progress=None, timing: dict | None = None) -> list[Page]:
    """progress(done, total), if given, is called as each page's OCR finishes (real completion order under
    concurrency, not necessarily page order) - for a live "page N of M" indicator. The returned Page list is
    always in page order regardless. timing, if given, gets timing["render"] and timing["ocr"] (seconds, this
    call's own rendering and OCR phases) added to it - not overwritten, so a caller loading more than one PDF
    (the RFQ/spec files, then the pack) can pass the same dict and see each phase's total across all of them."""
    t0 = time.time()
    stem = pdf.stem.replace(" ", "_") + "-" + sha1_file(pdf)[:8]
    imgdir = workdir / stem
    pngs = render_pdf(pdf, imgdir)
    if timing is not None:
        timing["render"] = timing.get("render", 0.0) + (time.time() - t0)
    t1 = time.time()
    want = pdf_page_count(pdf)
    if len(pngs) != want:
        raise RuntimeError(
            f"{pdf.name}: read {len(pngs)} page(s) but the PDF has {want}. Stopping rather than auditing a "
            "partial pack - delete the cached render under .cache and try again.")
    ocr_dir = workdir / "ocr"
    jobs = []  # (page number, png path, content hash, cache path)
    for i, png in enumerate(pngs, 1):
        sha = sha1_file(png)
        cache = ocr_dir / f"{sha}.json"
        legacy = png.with_suffix(".ocr.json")
        if not cache.exists() and legacy.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(legacy, cache)
        jobs.append((i, png, sha, cache))

    workers = _ocr_workers()
    results: list = [None] * len(jobs)
    if workers > 1 and len(jobs) > 1:
        # a thread pool, not a process pool: pytesseract shells out to the real tesseract binary via
        # subprocess.Popen, releasing the GIL while it waits, so threads already get genuine concurrent
        # OCR processes. A process pool was tried first, but on macOS (and anywhere the default start
        # method is "spawn") a worker process re-executes this program's own entry point to set itself up -
        # which is app.py's whole top-level script when run under Streamlit (it has no
        # `if __name__ == "__main__":` guard, nor should it), so every OCR call crashed the app.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(ocr_image, png, cache): idx for idx, (_, png, _, cache) in enumerate(jobs)}
            done = 0
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
                done += 1
                if progress:
                    progress(done, len(jobs))
    else:
        for idx, (_, png, _, cache) in enumerate(jobs):
            results[idx] = ocr_image(png, cache)
            if progress:
                progress(idx + 1, len(jobs))

    if timing is not None:
        timing["ocr"] = timing.get("ocr", 0.0) + (time.time() - t1)
    return [Page(i, str(png), text, words, sha=sha)
           for (i, png, sha, _), (text, words) in zip(jobs, results)]


def first_page_text(pdf: Path, workdir: Path) -> str:
    """Enough text to tell a buyer's RFQ email/letter apart from their specification sheet, without
    rendering and OCRing the whole document: the first page's own text layer (fast, most RFQ covering
    emails/letters have one), or - for a scan with no text layer, such as a specification sheet that is
    itself a scanned, signed-off form - that one page rendered and OCR'd."""
    out = subprocess.run(["pdftotext", "-layout", "-f", "1", "-l", "1", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    if len(out.strip()) >= 20:
        return out
    tmp = workdir / f"classify-{sha1_file(pdf)[:8]}"
    tmp.mkdir(parents=True, exist_ok=True)
    png = tmp / "p-1.png"
    if not png.exists() or not _usable_png(png):
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", "1", "-l", "1", str(pdf), str(tmp / "p")],
                       check=True)
    pngs = sorted(tmp.glob("p-*.png"))
    return ocr_image(pngs[0])[0] if pngs else ""


# A buyer's own RFQ-bundle document (the covering email/letter, or the specification sheet) is classified
# by wording each conventionally carries in South African government procurement documents generally - not
# anything specific to one buyer's tender - so this works on a document this code has never seen.
_RFQ_EMAIL_HINTS = re.compile(
    r"closing date|quotation (?:date|reference)|request for (?:procurement|quotation)|"
    r"quotations? (?:must|which)|\bRFQ\b|central supplier database", re.I)
_SPEC_HINTS = re.compile(r"SPECS VERIFIED|\bMOTIVATION\b|Description\s+Quantity|Delivery adress|Delivery address", re.I)


def classify_rfq_document(text: str) -> str | None:
    """'email' (the RFQ covering letter), 'spec' (the specification sheet), or None if the wording in the
    first page isn't a clear enough signal either way - the caller should ask, not guess."""
    t = text[:3000]
    spec_hit, email_hit = bool(_SPEC_HINTS.search(t)), bool(_RFQ_EMAIL_HINTS.search(t))
    if spec_hit and not email_hit:
        return "spec"
    if email_hit and not spec_hit:
        return "email"
    return None


# --- classification -------------------------------------------------------

# Ordered: first match wins. Keywords are matched against upper-cased OCR text of
# the first ~600 characters, because continuation pages repeat nothing useful.
SIGNATURES = [
    ("sbd6_1", [r"SBD\s*6\.1", r"PREFERENCE POINTS CLAIM"]),
    ("sbd6_2", [r"SBD\s*6\.2", r"DECLARATION CERTIFICATE FOR LOCAL"]),
    ("local_content_decl", [r"REFER TO ANNEX\s*B", r"LOCAL CONTENT DECLARATION BY CHIEF"]),
    ("sbd4", [r"SBD\s*4", r"DECLARATION OF INTEREST"]),
    ("sbd8", [r"SBD\s*8", r"PAST SUPPLY CHAIN"]),
    ("sbd9", [r"SBD\s*9", r"INDEPENDENT BID DETERMINATION"]),
    ("tcs", [r"TAX COMPLIANCE", r"TCS\s+DETAILS"]),
    ("csd", [r"CSD REGISTRATION", r"CENTRAL SUPPLIER", r"BBBEE INFORMATION", r"SUPPLIER DIRECTOR"]),
    ("bbbee_affidavit", [r"SWORN AFFIDAVIT", r"EXEMPTED MICRO ENTERPRISE"]),
    ("cipc", [r"COR\s?14", r"REGISTRATION CERTIFICATE", r"COMMISSIONER OF COMPANIES"]),
    ("quote", [r"REQUEST FOR PROCUREMENT", r"QUOTATION DATE", r"QUOTE REFERENCE"]),
    ("spec", [r"SPECS VERIFIED", r"MOTIVATION"]),
]

CONTINUATION_HINTS = {
    # page-number footers / mid-document cues for pages with no header
    "sbd6_2": [r"STIPULATED MINIMUM", r"IMPORTED CONTENT", r"CAN BE DISQUALIFIED|A BID MAY BE DISQUALIFIED"],
    "local_content_decl": [r"LOCAL CONTENT PERCENTAGES", r"PROCUREMENT AUTHORITY", r"WITNESS"],
    "csd": [r"CSD REGISTRATION", r"COMMODIT"],
    "sbd6_1": [r"PAGE\s*\d\s*OF\s*5", r"B-BBEE STATUS LEVEL", r"WITNESSES", r"SUB-CONTRACTING"],
    "sbd4": [r"STATE EMPLOYEE", r"DECLARATION", r"PERSAL"],
    "sbd8": [r"CERTIFICATION", r"DEFAULTERS", r"CONVICTED"],
    "sbd9": [r"COMPETITOR", r"COLLUSIVE", r"COMPETITION COMMISSION"],
}


# --- blank-form templates: a page can be recognised, and placed within its form, by the text it shares ---

TEMPLATE_KEYS = [("sbd6_2", r"6[_.\- ]?2"), ("sbd6_1", r"6[_.\- ]?1"), ("sbd4", r"SBD[_ ]?4"), ("sbd8", r"SBD[_ ]?8"),
                 ("sbd9", r"SBD[_ ]?9")]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]{3,}", text.lower())


class Template:
    """A blank form as a sequence of words. A pack page that shares word triples with it can be located in it."""

    def __init__(self, text: str):
        self.tokens = _tokens(text)
        self.index: dict[tuple, list[int]] = {}
        for i in range(len(self.tokens) - 2):
            self.index.setdefault(tuple(self.tokens[i:i + 3]), []).append(i)

    def locate(self, page_text: str):
        """(position 0..1 of the page within the form, number of matching word triples)."""
        toks = _tokens(page_text)
        hits = []
        for i in range(len(toks) - 2):
            pos = self.index.get(tuple(toks[i:i + 3]))
            if pos and len(pos) <= 3:  # triples that occur once or twice in the form say where we are
                hits.append(pos[0] if len(pos) == 1 else sorted(pos)[len(pos) // 2])
        if not hits:
            return None, 0
        hits.sort()
        return hits[len(hits) // 2] / max(1, len(self.tokens)), len(hits)


def load_templates(blank_dir) -> dict[str, Template]:
    out = {}
    for f in sorted(Path(blank_dir).glob("*.txt")):
        for dtype, pat in TEMPLATE_KEYS:
            if re.search(pat, f.stem, re.I):
                out[dtype] = Template(f.read_text())
                break
    return out


def classify_pages(pages: list[Page], templates: dict | None = None) -> None:
    prev = "unknown"
    for p in pages:
        if len(p.text.strip()) < 20 and len(p.words) <= 3:
            p.doc_type = "blank"  # nothing printed: a blank sheet, and it must not borrow the form before it
            continue
        head = p.text[:700].upper()
        found = None
        for dtype, pats in SIGNATURES:
            if any(re.search(pt, head) for pt in pats):
                found = dtype
                break
        if not found and templates:
            # a page with no header: which blank form does its wording come from? (robust to pages that were moved)
            sc = sorted(((t.locate(p.text)[1], d) for d, t in templates.items()), reverse=True)
            if sc and sc[0][0] >= 8 and sc[0][0] >= 2 * (sc[1][0] if len(sc) > 1 else 0):
                found = sc[0][1]
        if not found:
            body = p.text.upper()
            for dtype, pats in CONTINUATION_HINTS.items():
                if prev == dtype and any(re.search(pt, body) for pt in pats):
                    found = dtype
                    break
        if not found:
            # whole-page search for the distinctive headers (OCR may garble the first lines)
            body = p.text.upper()
            for dtype, pats in SIGNATURES:
                if any(re.search(pt, body) for pt in pats[:1]):
                    found = dtype
                    break
        p.doc_type = found or (prev if prev != "unknown" else "unknown")
        prev = p.doc_type
