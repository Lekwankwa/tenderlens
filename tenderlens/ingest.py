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

from .llm import slices

# Resolution pages are rendered at. TENDERLENS_RENDER_DPI changes it for experiments (a PDF of 150 PPI scans gains no detail above 150).
DPI = max(72, min(400, int(os.environ["TENDERLENS_RENDER_DPI"]))) if os.environ.get("TENDERLENS_RENDER_DPI", "").strip().isdigit() else 150


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


def fast_render_enabled() -> bool:
    """On by default (TENDERLENS_FAST_RENDER=0 turns it off): a page that is one JPEG scanned at the render resolution is
    copied out of the PDF instead of being decoded, re-sampled and re-encoded by pdftoppm. About 50 times faster per page.
    The image is not pixel-identical to pdftoppm's (mean grey difference 1.6-7, an occasional 1 px size difference) and the OCR
    text differs, but a nine-grade run (18 packs) against the pdftoppm pipeline showed no loss: strict detection 69 vs 70 of 383,
    clean-pack issue-tier findings 150 vs 140. Only pages that pass _fast_pages' checks are copied; every other page is rendered
    by pdftoppm as before."""
    return os.environ.get("TENDERLENS_FAST_RENDER", "1").strip() != "0"


def _fast_pages(pdf: Path, dpi: int) -> set[int]:
    """Pages that qualify for the fast path: exactly one image on the page, a JPEG, 8-bit grey or RGB, native resolution within 3%
    of `dpi` in both directions, and a pixel size within 3 px of the page size at `dpi`."""
    info = subprocess.run(["pdfinfo", "-f", "1", "-l", "100000", str(pdf)], capture_output=True, text=True).stdout
    size = {int(m.group(1)): (float(m.group(2)), float(m.group(3)))
            for m in re.finditer(r"Page\s+(\d+)\s+size:\s+([\d.]+) x ([\d.]+) pts", info)}
    if not size:
        m = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info)
        n = pdf_page_count(pdf)
        size = {i: (float(m.group(1)), float(m.group(2))) for i in range(1, n + 1)} if m else {}
    rot = {int(m.group(1)): int(m.group(2)) for m in re.finditer(r"Page\s+(\d+)\s+rot:\s+(\d+)", info)}
    rows: dict[int, list[list[str]]] = {}
    for line in subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True).stdout.splitlines()[2:]:
        cols = line.split()
        if len(cols) >= 14 and cols[0].isdigit():
            rows.setdefault(int(cols[0]), []).append(cols)
    ok = set()
    for page, imgs in rows.items():
        if len(imgs) != 1 or page not in size or rot.get(page, 0) != 0:
            continue
        c = imgs[0]                      # page num type width height color comp bpc enc interp object ID x-ppi y-ppi size ratio
        try:
            w, h, enc, xppi, yppi = int(c[3]), int(c[4]), c[8], float(c[12]), float(c[13])
        except (ValueError, IndexError):
            continue
        want_w, want_h = size[page][0] / 72 * dpi, size[page][1] / 72 * dpi
        if (c[2] == "image" and enc == "jpeg" and c[5] in ("gray", "rgb") and c[7] == "8"
                and abs(xppi - dpi) <= 0.03 * dpi and abs(yppi - dpi) <= 0.03 * dpi
                and abs(w - want_w) <= 3 and abs(h - want_h) <= 3):
            ok.add(page)
    return ok


def _extract_page_image(pdf: Path, page: int, target: Path) -> bool:
    """Copy page's single JPEG out of the PDF and save it as a PNG at `target`. False if anything is not as expected."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["pdfimages", "-j", "-f", str(page), "-l", str(page), str(pdf), str(Path(tmp) / "img")],
                           capture_output=True)
        found = sorted(Path(tmp).glob("img-*.jpg"))
        if r.returncode != 0 or len(found) != 1:
            return False
        try:
            with Image.open(found[0]) as im:
                im.load()
                im.save(target, format="PNG", compress_level=1)
        except Exception:  # noqa: BLE001 - an image PIL cannot handle falls back to pdftoppm
            return False
    return True


def _stretches(numbers: list[int]) -> list[tuple[int, int]]:
    """[3, 4, 5, 9] -> [(3, 5), (9, 9)]: runs of consecutive page numbers, so one pdftoppm call covers each run."""
    out: list[tuple[int, int]] = []
    for n in numbers:
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def render_chunk_size() -> int:
    """Pages one render+OCR worker takes at a time (TENDERLENS_CHUNK_PAGES, 1 to 50). A chunk is rendered with ONE pdftoppm
    call and then OCR'd page by page by the same worker, so a big PDF is opened once per chunk, not once per page."""
    raw = os.environ.get("TENDERLENS_CHUNK_PAGES", "").strip()
    return max(1, min(50, int(raw))) if raw.isdigit() else DEFAULT_CHUNK_PAGES


DEFAULT_CHUNK_PAGES = 1


def render_and_ocr(pdf: Path, outdir: Path, ocr_dir: Path, workers: int, progress=None, dpi: int = DPI, on_page=None,
                   chunk: int | None = None):
    """Render and OCR a PDF with no wait in between. The pages are split into chunks (render_chunk_size() pages); a worker
    renders its chunk with one pdftoppm call (the same files and images a whole-document run gives) and OCRs each page of
    it straight away, so OCR starts with the first finished chunk instead of after the last page. Pages are written straight
    into outdir: a half-finished render is never trusted later, because _render_cached only accepts a folder holding exactly
    as many usable images as the PDF has pages. on_page(number, png, sha, text, words), if given, is called from the worker
    thread the moment a page's OCR is in (in no particular page order), so later stages can start on it while others render.
    Returns (page images in page order, [(text, words)] in page order, [content hash], busy seconds rendering, busy seconds
    in OCR) - the busy times are summed across workers."""
    from concurrent.futures import ThreadPoolExecutor
    outdir.mkdir(parents=True, exist_ok=True)
    want = pdf_page_count(pdf)
    for p in outdir.glob("p-*.png"):
        p.unlink()
    size = chunk or render_chunk_size()
    ranges = [(a, min(want, a + size - 1)) for a in range(1, want + 1, size)]
    fast = _fast_pages(pdf, dpi) if fast_render_enabled() else set()
    digits = len(str(want))                      # pdftoppm numbers its files p-1 .. p-N padded to the page count's width
    busy = {"render": 0.0, "ocr": 0.0}
    got: dict[int, tuple] = {}
    lock = threading.Lock()

    def work(first: int, last: int) -> None:
        t0 = time.time()
        slow = []
        for n in range(first, last + 1):
            if n in fast and _extract_page_image(pdf, n, outdir / f"p-{n:0{digits}d}.png"):
                continue
            slow.append(n)
        # the pages that are not eligible (or could not be copied out) are rendered by pdftoppm, one run per stretch
        for lo, hi in _stretches(slow):
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", str(lo), "-l", str(hi), str(pdf), str(outdir / "p")],
                           check=True, capture_output=True)
        rendered = time.time() - t0
        files = {int(f.stem.split("-")[1]): f for f in outdir.glob("p-*.png") if first <= int(f.stem.split("-")[1]) <= last}
        with lock:
            busy["render"] += rendered
        for n in range(first, last + 1):
            png = files.get(n)
            if png is None or not _usable_png(png):
                raise RuntimeError(f"pdftoppm did not produce a usable page {n} from {pdf.name}.")
            t1 = time.time()
            sha = sha1_file(png)
            cache = ocr_dir / f"{sha}.json"
            legacy = png.with_suffix(".ocr.json")
            if not cache.exists() and legacy.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(legacy, cache)
            text, words = ocr_image(png, cache)
            with lock:
                busy["ocr"] += time.time() - t1
                got[n] = (png, sha, (text, words))
                done = len(got)
            if on_page:
                on_page(n, png, sha, text, words)
            if progress:
                progress(done, want)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ranges)))) as ex:
        for fut in [ex.submit(work, a, b) for a, b in ranges]:
            fut.result()
    if len(got) != want:
        raise RuntimeError(f"{pdf.name}: rendered and read {len(got)} page(s) but the PDF has {want}.")
    order = sorted(got)
    return ([got[n][0] for n in order], [got[n][2] for n in order], [got[n][1] for n in order],
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


def load_pdf(pdf: Path, workdir: Path, progress=None, timing: dict | None = None, on_page=None) -> list[Page]:
    """progress(done, total), if given, is called as each page's OCR finishes (real completion order under
    concurrency, not necessarily page order) - for a live "page N of M" indicator. The returned Page list is
    always in page order regardless. timing, if given, gets timing["render"] and timing["ocr"] (seconds, this
    call's own rendering and OCR phases) added to it - not overwritten, so a caller loading more than one PDF
    (the RFQ/spec files, then the pack) can pass the same dict and see each phase's total across all of them.
    on_page(page), if given, is called once per page as soon as that page's OCR is in (from a worker thread while the other
    pages are still being rendered and read, so it must be thread-safe); the page has no doc_type yet."""
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
        pngs, results, shas, r_busy, o_busy = render_and_ocr(
            pdf, imgdir, ocr_dir, workers, progress=progress,
            on_page=(lambda n, png, sha, text, words: on_page(Page(n, str(png), text, words, sha=sha))) if on_page else None)
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
    out = [Page(i, str(png), text, words, sha=sha) for (i, png, sha, _), (text, words) in zip(jobs, results)]
    if on_page:
        for page in out:
            on_page(page)
    return out


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
    ("sbd6_1", [r"[SM]BD\s*6\.1", r"PREFERENCE POINTS CLAIM"]),
    ("sbd6_2", [r"[SM]BD\s*6\.2", r"DECLARATION CERTIFICATE FOR LOCAL"]),
    ("local_content_decl", [r"REFER TO ANNEX\s*B", r"LOCAL CONTENT DECLARATION BY CHIEF"]),
    # Annex C (SATS 1286:2011): its own printed subtitle, "Local Content Declaration - Summary Schedule"
    # (a dash), which OCRs reliably even when the stylised "Annex C" banner above it does not. SBD 6.2
    # clause 1.6 cites the same words in a sentence with a colon instead ("...the Annex C (local content
    # declaration: summary schedule) are not submitted...") - the dash is what tells them apart.
    ("annexure_c", [r"ANNEX\s*C\s*[\r\n]*\s*(?:-\s*)?(?:LOCAL CONTENT DECLARATION|SUMMARY SCHEDULE)",
                    r"LOCAL CONTENT DECLARATION\s*-\s*SUMMARY SCHEDULE"]),
    ("sbd4", [r"[SM]BD\s*4", r"DECLARATION OF INTEREST"]),
    ("sbd8", [r"[SM]BD\s*8", r"PAST SUPPLY CHAIN"]),
    ("sbd9", [r"[SM]BD\s*9", r"INDEPENDENT BID DETERMINATION"]),
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

TEMPLATE_KEYS = [("sbd6_2", r"6[_.\- ]?2"), ("sbd6_1", r"6[_.\- ]?1"), ("sbd4", r"[SM]BD[_ ]?4"), ("sbd8", r"[SM]BD[_ ]?8"),
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


# How many pages in a row one form type may cover when pages with no header of their own are given the form before them.
# Without a limit, every page after one SBD 9 header became "SBD 9" (62 pages of a 134-page civil engineering pack). The
# SBD forms' own page counts plus one (a duplicated page is still caught); the other forms are generous.
INHERIT_LIMIT = {"sbd6_1": 6, "sbd6_2": 4, "local_content_decl": 3, "sbd4": 5, "sbd8": 3, "sbd9": 5,
                 "csd": 6, "tcs": 3, "bbbee_affidavit": 3, "cipc": 3}
SBD_TYPES = ("sbd4", "sbd6_1", "sbd6_2", "sbd8", "sbd9", "local_content_decl", "annexure_c")
# municipalities issue the same standard forms as "MBD" (MBD 4, MBD 6.1, MBD 9), so either prefix designates them
_SBD_MENTION = re.compile(r"\b[SM]BD\s*[1-9](?:\.\d)?\b", re.I)


def tender_designates_sbd(tender_text: str | None) -> bool | None:
    """Does the buyer's own text name the standard forms (SBD 4, SBD 6.1, or the municipal MBD 4, MBD 6.1, ...)? True/False when there is tender text to read,
    None when there is none (a scan with no text layer) - the caller then has to look at the pack instead."""
    if tender_text is None or len(tender_text.strip()) < 200:
        return None
    return bool(_SBD_MENTION.search(tender_text))


def sbd_header_pages(pages: list[Page]) -> int:
    """Pages whose own first lines carry an SBD form header (not pages that merely inherited the type)."""
    return sum(1 for p in pages if p.doc_type in SBD_TYPES and any(
        re.search(pt, p.text[:700].upper()) for d, pats in SIGNATURES if d in SBD_TYPES for pt in pats))


# A page with no header of its own takes the form before it only if it still looks like that form: for the forms that have a
# blank template (SBD 4, 6.1, 8, 9) it must share enough wording with the blank (continuation pages of a real SBD 6.1 score
# 50-190; unrelated tender pages that merely followed it scored 0-21); for the others it must carry one of the form's topic
# words. Without this a Supplier Code of Conduct page after SBD 9 became "SBD 9" and was checked for an SBD 9 signature.
INHERIT_MIN_TEMPLATE_SCORE = 30
INHERIT_TOPIC_WORDS = {
    "bbbee_affidavit": r"AFFIDAVIT|DEPONENT|COMMISSIONER|SWORN|OATH|ENTERPRISE|TURNOVER|BLACK OWN",
    "csd": r"SUPPLIER|CSD|REGISTRATION|DIRECTOR|TAX|B-?BBEE",
    "tcs": r"TAX|SARS|COMPLIANCE|TCS|PIN",
    "cipc": r"COMPAN|DIRECTOR|REGISTRATION|SHARE|CIPC|COR\s?14",
}


def _looks_like(dtype: str, page: Page, templates: dict | None) -> bool:
    tmpl = (templates or {}).get(dtype)
    if tmpl is not None:
        return tmpl.locate(page.text)[1] >= INHERIT_MIN_TEMPLATE_SCORE
    topic = INHERIT_TOPIC_WORDS.get(dtype)
    return True if topic is None else bool(re.search(topic, page.text.upper()))


FORM_TYPES = SBD_TYPES + ("bbbee_affidavit",)


def is_struck_through(page: Page, min_fraction: float = 0.45) -> bool:
    """A long diagonal line across the page: a bidder cancelling a form that does not apply ("not applicable", an unused
    affidavit). Measured as the longest line between 15 and 75 degrees (either way) over the page's short side: 0 on
    ordinary pages (the SA sample, 83 and 139 CE pages), 0.55-1.12 on the three voided forms seen."""
    import cv2
    import numpy as np
    gray = cv2.imread(str(page.image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return False
    h, w = gray.shape
    _, ink = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8))
    lines = cv2.HoughLinesP(ink, 1, np.pi / 180, 150, minLineLength=int(min_fraction * min(w, h)), maxLineGap=25)
    for ln in (lines if lines is not None else []):
        x1, y1, x2, y2 = (int(v) for v in np.ravel(ln))
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        if 15 <= ang <= 165 and not 75 <= ang <= 105 and np.hypot(x2 - x1, y2 - y1) >= min_fraction * min(w, h):
            return True
    return False


def classify_pages(pages: list[Page], templates: dict | None = None) -> None:
    _classify_by_text(pages, templates)
    for p in pages:
        # a form that has been struck through is not a completed form: no signature, stamp or entry rule should judge it
        if p.doc_type in FORM_TYPES and p.image_path and Path(str(p.image_path)).exists() and is_struck_through(p):
            p.doc_type = "voided"


def _classify_by_text(pages: list[Page], templates: dict | None = None) -> None:
    prev, run = "unknown", 0
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
            # (the form's distinctive header AND one more of its cues: a tender page that merely mentions "sworn affidavit" in a
            # table is not an affidavit)
            body = p.text.upper()
            for dtype, pats in SIGNATURES:
                if re.search(pats[0], body) and (len(pats) < 2 or any(re.search(pt, body) for pt in pats[1:])):
                    found = dtype
                    break
        if found == "bbbee_affidavit" and not re.search(r"AFFIDAVIT|DEPONENT|COMMISSIONER|OATH", p.text.upper()):
            # "Exempted Micro Enterprise" is also the heading of the issued B-BBEE certificate, which has no deponent, stamp or
            # signature line; only a page that talks about an affidavit, a deponent or a commissioner is the sworn form
            found = "bbbee_certificate"
        if found:
            run = run + 1 if found == prev else 1
            p.doc_type = found
        elif prev != "unknown" and run < INHERIT_LIMIT.get(prev, 2) and _looks_like(prev, p, templates):
            p.doc_type, run = prev, run + 1
        else:
            p.doc_type, run = "unknown", 0
        prev = p.doc_type


def near_duplicate_pairs(pages: list[Page], min_corr: float = 0.97, min_overlap: float = 0.80) -> list[tuple[int, int, float, float]]:
    """(page a, page b, image correlation, shared-word fraction) for neighbouring pages that are scans of the same sheet.
    Planted duplicates in the CE packs are separate scans, so their files are not byte-identical and the exact-hash check
    cannot see them. Two pages count as the same sheet only when the page images match closely (correlation of a 96x128
    greyscale copy) AND their OCR words overlap strongly, and only for neighbours (or one page apart, a blank in between).
    Tuned on six packs (2 of 3 planted duplicates found, no clean-pack hit): consecutive financial statements or
    reference forms can look alike, so the result is a lead for a person, never proof."""
    import numpy as np

    def vec(page: Page):
        im = Image.open(page.image_path).convert("L").resize((96, 128), Image.BILINEAR)
        a = 255 - np.asarray(im, dtype=float)
        a -= a.mean()
        return (a / (np.linalg.norm(a) or 1)).ravel()

    def words(page: Page) -> set[str]:
        return set(re.findall(r"[a-z0-9]{3,}", page.text.lower()))

    live = [p for p in pages if p.doc_type != "blank" and len(p.text.strip()) >= 150]
    out = []
    for i, a in enumerate(live):
        for b in live[i + 1:i + 3]:
            if b.number - a.number > 2:
                continue
            wa, wb = words(a), words(b)
            overlap = len(wa & wb) / max(1, len(wa | wb))
            if overlap < min_overlap:
                continue
            corr = float(vec(a) @ vec(b))
            if corr >= min_corr:
                out.append((a.number, b.number, round(corr, 3), round(overlap, 2)))
    return out
