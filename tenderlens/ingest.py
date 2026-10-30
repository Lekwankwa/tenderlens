"""PDF -> page images -> OCR (with word boxes and confidences) -> page classification."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
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
        # One pdftoppm per CPU, each on its own contiguous page range, all at once. Output is byte-identical
        # to a single run: pdftoppm numbers (and zero-pads) files by the page's position in the whole PDF.
        n = max(1, min(_ocr_workers(), want))
        step = -(-want // n)
        procs = [subprocess.Popen(["pdftoppm", "-r", str(dpi), "-png", "-f", str(first),
                                   "-l", str(min(want, first + step - 1)), str(pdf), str(tmp / "p")])
                 for first in range(1, want + 1, step)]
        for p in procs:
            p.wait()  # a range that failed shows up as missing pages in the count check just below
        rendered = sorted(tmp.glob("p-*.png"))
        if len(rendered) != want or not all(_usable_png(p) for p in rendered):
            raise RuntimeError(
                f"pdftoppm rendered {len(rendered)} usable page(s) from {pdf.name}, but pdfinfo reports {want}.")
        for p in rendered:
            shutil.move(str(p), str(outdir / p.name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sorted(outdir.glob("p-*.png"))


def _render_cached(pdf: Path, outdir: Path) -> list[Path] | None:
    """The cached page images for `pdf` when they are all there and usable, else None (see render_pdf)."""
    want = pdf_page_count(pdf)
    existing = sorted(outdir.glob("p-*.png")) if outdir.exists() else []
    if existing and len(existing) == want and all(_usable_png(p) for p in existing):
        return existing
    return None


def render_and_ocr(pdf: Path, outdir: Path, ocr_dir: Path, workers: int, progress=None, dpi: int = DPI):
    """Render and OCR a PDF with no wait in between: each worker renders ONE page (pdftoppm -f N -l N, which
    names the file exactly as a whole-document run does and produces the same image) and OCRs it straight away,
    so OCR starts with the first finished page instead of after the last. Same atomic placement as render_pdf:
    pages go into a temporary sibling folder and are moved into outdir only once every page is usable.
    Returns (page images in page order, [(text, words)] in page order, [content hash], busy seconds rendering,
    busy seconds in OCR) - the busy times are summed across workers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    outdir.mkdir(parents=True, exist_ok=True)
    want = pdf_page_count(pdf)
    for p in outdir.glob("p-*.png"):
        p.unlink()
    tmp = outdir.parent / f".{outdir.name}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    busy = {"render": 0.0, "ocr": 0.0}
    lock = threading.Lock()

    def one(n: int):
        t0 = time.time()
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", str(n), "-l", str(n), str(pdf), str(tmp / "p")],
                       check=True, capture_output=True)
        found = list(tmp.glob("p-*.png"))
        png = next((f for f in found if int(f.stem.split("-")[1]) == n), None)
        if png is None or not _usable_png(png):
            raise RuntimeError(f"pdftoppm did not produce a usable page {n} from {pdf.name}.")
        sha = sha1_file(png)
        t1 = time.time()
        cache = ocr_dir / f"{sha}.json"
        legacy = png.with_suffix(".ocr.json")
        if not cache.exists() and legacy.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(legacy, cache)
        result = ocr_image(png, cache)
        with lock:
            busy["render"] += t1 - t0
            busy["ocr"] += time.time() - t1
        return n, png, sha, result

    got: dict[int, tuple] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, want))) as ex:
            for fut in as_completed([ex.submit(one, n) for n in range(1, want + 1)]):
                n, png, sha, result = fut.result()
                got[n] = (png, sha, result)
                if progress:
                    progress(len(got), want)
        for n in sorted(got):
            shutil.move(str(got[n][0]), str(outdir / got[n][0].name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    order = sorted(got)
    return ([outdir / got[n][0].name for n in order], [got[n][2] for n in order], [got[n][1] for n in order],
            busy["render"], busy["ocr"])


def sha1_file(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def _tsv_words(tsv: str) -> list[Word]:
    """The words in a Tesseract TSV (one row per word, box and confidence), parsed exactly as pytesseract's
    image_to_data does: every column but the last (the text) read as int(float(...)), and a final row whose
    empty text cell was dropped by Tesseract padded back out."""
    rows = [r.split("\t") for r in tsv.strip().split("\n")]
    if len(rows) < 2:
        return []
    header, words = rows[0], []
    col = {name: i for i, name in enumerate(header)}
    for row in rows[1:]:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        t = row[col["text"]].strip()
        if not t:
            continue
        n = lambda k: int(float(row[col[k]]))  # noqa: E731
        words.append(Word(t, float(n("conf")), n("left"), n("top"), n("width"), n("height")))
    return words


def ocr_image(png: Path, cache: Path | None = None) -> tuple[str, list[Word]]:
    """OCR one page image. The cache is keyed by the image's content hash, so a page that appears
    unchanged in a modified pack is never read twice (and always reads the same way).
    One Tesseract run writes both outputs (plain text and the word-box TSV). This used to be two separate
    runs over the same image - the same result, twice the CPU - and the dominant cost of every audit."""
    if cache and cache.exists():
        d = json.loads(cache.read_text())
        return d["text"], [Word(**w) for w in d["words"]]
    import tempfile
    img = Image.open(png)
    if "A" in img.getbands():  # as pytesseract does: a transparent page is read on a white background
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, (0, 0), img.getchannel("A"))
        img = flat
    with tempfile.TemporaryDirectory() as tmp:
        # The image is re-saved to a temporary PNG first - exactly what pytesseract did on every call - and NOT
        # handed to Tesseract as the original file. Re-saving drops the PNG's resolution tag, and Tesseract reads
        # a page differently with and without it (a different word count on a test page), so skipping this
        # step would silently change every page's OCR text.
        src = Path(tmp) / "in.png"
        img.save(src, format="PNG")
        base = Path(tmp) / "out"
        proc = subprocess.run(["tesseract", str(src), str(base), "--psm", "4", "txt", "tsv"],
                              capture_output=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed on {Path(png).name}: {proc.stderr.decode('utf-8', 'replace')[:300]}")
        text = base.with_suffix(".txt").read_text(encoding="utf-8")
        words = _tsv_words(base.with_suffix(".tsv").read_text(encoding="utf-8"))
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"text": text, "words": [w.__dict__ for w in words]}))
    return text, words


def _available_cpus() -> float:
    """CPUs this process may really use. os.cpu_count() is the machine's, not the container's: on Cloud Run a
    2-vCPU service can still report many more, so the container's CPU quota (cgroup v2, then v1) caps it."""
    n = float(os.cpu_count() or 1)
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            n = min(n, int(quota) / int(period))
    except (OSError, ValueError):
        try:
            quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if quota > 0:
                n = min(n, quota / period)
        except (OSError, ValueError):
            pass
    return max(n, 1.0)


def _ocr_workers() -> int:
    """Pages OCR'd at once: TENDERLENS_OCR_WORKERS if set, otherwise one per available CPU (one fewer than all
    of them on a big machine, so a laptop stays usable), at most 8. Each Tesseract process is pinned to one
    thread (see ocr_image), so this is the whole parallelism."""
    raw = os.environ.get("TENDERLENS_OCR_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    cpus = _available_cpus()
    return max(1, min(8, int(cpus) if cpus <= 4 else int(cpus) - 1))


def load_pdf(pdf: Path, workdir: Path, progress=None, timing: dict | None = None) -> list[Page]:
    """progress(done, total), if given, is called as each page's OCR finishes (real completion order under
    concurrency, not necessarily page order) - for a live "page N of M" indicator. The returned Page list is
    always in page order regardless. timing, if given, gets timing["render"] and timing["ocr"] (seconds, this
    call's own rendering and OCR phases) added to it - not overwritten, so a caller loading more than one PDF
    (the RFQ/spec files, then the pack) can pass the same dict and see each phase's total across all of them."""
    t0 = time.time()
    stem = pdf.stem.replace(" ", "_") + "-" + sha1_file(pdf)[:8]
    imgdir = workdir / stem
    ocr_dir = workdir / "ocr"
    workers = _ocr_workers()
    pngs = _render_cached(pdf, imgdir)
    # TENDERLENS_STREAM_OCR=0 goes back to rendering every page before the first is read
    streaming = os.environ.get("TENDERLENS_STREAM_OCR", "1").strip() != "0"
    if pngs is None and streaming and workers > 1 and pdf_page_count(pdf) > 1:
        # Not rendered yet: render and OCR each page back to back on the same workers (see render_and_ocr). The two
        # phases overlap, so their times are the workers' busy seconds spread over the workers - roughly their
        # share of the wall clock, adding up to about the real total.
        print(f"ocr pages={pdf_page_count(pdf)} workers={workers} cpus={_available_cpus():.1f} streaming", flush=True)
        pngs, results, shas, r_busy, o_busy = render_and_ocr(pdf, imgdir, ocr_dir, workers, progress=progress)
        if timing is not None:
            timing["render"] = timing.get("render", 0.0) + r_busy / workers
            timing["ocr"] = timing.get("ocr", 0.0) + o_busy / workers
        return [Page(i, str(png), text, words, sha=sha)
                for i, (png, (text, words), sha) in enumerate(zip(pngs, results, shas), 1)]
    if pngs is None:
        pngs = render_pdf(pdf, imgdir)
    if timing is not None:
        timing["render"] = timing.get("render", 0.0) + (time.time() - t0)
    t1 = time.time()
    want = pdf_page_count(pdf)
    if len(pngs) != want:
        raise RuntimeError(
            f"{pdf.name}: read {len(pngs)} page(s) but the PDF has {want}. Stopping rather than auditing a "
            "partial pack - delete the cached render under .cache and try again.")
    jobs = []  # (page number, png path, content hash, cache path)
    for i, png in enumerate(pngs, 1):
        sha = sha1_file(png)
        cache = ocr_dir / f"{sha}.json"
        legacy = png.with_suffix(".ocr.json")
        if not cache.exists() and legacy.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(legacy, cache)
        jobs.append((i, png, sha, cache))

    print(f"ocr pages={len(jobs)} workers={workers} cpus={_available_cpus():.1f}", flush=True)  # no content: just sizing
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


def ocr_text(pdf: Path, workdir: Path, max_pages: int = 12) -> str:
    """The text of a PDF with no text layer (a scan), by OCR: its first `max_pages` pages, which is where a buyer
    states what is required. Page images and OCR results are cached under workdir by the PDF's content hash."""
    outdir = workdir / f"rfq-ocr-{sha1_file(pdf)[:8]}"
    outdir.mkdir(parents=True, exist_ok=True)
    pngs = sorted(outdir.glob("p-*.png"))
    if not pngs or not all(_usable_png(p) for p in pngs):
        for p in pngs:
            p.unlink()
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", "1", "-l", str(max_pages), str(pdf),
                        str(outdir / "p")], check=True)
        pngs = sorted(outdir.glob("p-*.png"))
    return "\n\n".join(ocr_image(p, outdir / f"{p.stem}.ocr.json")[0] for p in pngs)


def find_spec_sheet(pdfs: list[Path], workdir: Path, max_pages: int = 3) -> Path | None:
    """The specification sheet among the buyer's uploaded RFQ/Tender documents, or None. The app takes one
    list of files, so nothing says which one is the spec - and it is usually a scan with no text layer, which
    plain text extraction reads as empty. A spec is short; a longer document is never taken for one."""
    for p in pdfs:
        try:
            if pdf_page_count(p) <= max_pages and classify_rfq_document(first_page_text(p, workdir)) == "spec":
                return p
        except Exception:  # noqa: BLE001 - an unreadable file is simply an ordinary RFQ document here
            continue
    return None


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
    # Annex C (SATS 1286:2011): its own printed subtitle, "Local Content Declaration - Summary Schedule"
    # (a dash), which OCRs reliably even when the stylised "Annex C" banner above it does not. SBD 6.2
    # clause 1.6 cites the same words in a sentence with a colon instead ("...the Annex C (local content
    # declaration: summary schedule) are not submitted...") - the dash is what tells them apart.
    ("annexure_c", [r"ANNEX\s*C\s*[\r\n]*\s*(?:-\s*)?(?:LOCAL CONTENT DECLARATION|SUMMARY SCHEDULE)",
                    r"LOCAL CONTENT DECLARATION\s*-\s*SUMMARY SCHEDULE"]),
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
