"""PDF -> page images -> OCR (with word boxes and confidences) -> page classification."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
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


def render_pdf(pdf: Path, outdir: Path, dpi: int = DPI) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    existing = sorted(outdir.glob("p-*.png"))
    if existing:
        return existing
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(outdir / "p")], check=True)
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


def load_pdf(pdf: Path, workdir: Path) -> list[Page]:
    stem = pdf.stem.replace(" ", "_") + "-" + sha1_file(pdf)[:8]
    imgdir = workdir / stem
    pngs = render_pdf(pdf, imgdir)
    ocr_dir = workdir / "ocr"
    pages = []
    for i, png in enumerate(pngs, 1):
        sha = sha1_file(png)
        cache = ocr_dir / f"{sha}.json"
        legacy = png.with_suffix(".ocr.json")
        if not cache.exists() and legacy.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(legacy, cache)
        text, words = ocr_image(png, cache)
        pages.append(Page(i, str(png), text, words, sha=sha))
    return pages


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
