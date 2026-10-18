"""Image-level evidence from the scanned pages (OpenCV).

These checks do not need a language model. They answer questions a reviewer would
answer by eye: is there ink where a signature should be, was template highlighting
left in, is there a mark that sits outside every place a bidder was meant to write.
All results are *candidates for human review*, never verdicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from .ingest import Page, Word


@dataclass
class Region:
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in page pixels (150 dpi)
    kind: str
    ink: int = 0
    note: str = ""


def _gray(page: Page) -> np.ndarray:
    return cv2.cvtColor(cv2.imread(page.image_path), cv2.COLOR_BGR2GRAY)


def ink_mask(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return ink


def _components(mask: np.ndarray, min_area=1):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a >= min_area:
            yield x, y, w, h, a


# --- signatures -----------------------------------------------------------

_RESCAN: dict[str, list[Word]] = {}


def _rescan_lower_half(page: Page) -> list[Word]:
    """OCR the lower part of the page again, enlarged and as sparse text. The first pass reads the whole page in one
    go and can lose a short label (Signature, Date) that sits near handwriting; this pass is slower, so it is used
    only when a label that should be there was not found."""
    if page.sha in _RESCAN:
        return _RESCAN[page.sha]
    import pytesseract
    from PIL import Image
    im = Image.open(page.image_path).convert("L")
    top = int(im.height * 0.40)
    crop = im.crop((0, top, im.width, im.height)).resize((im.width * 2, (im.height - top) * 2))
    d = pytesseract.image_to_data(crop, config="--psm 11", output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if t:
            out.append(Word(t, float(d["conf"][i]), d["left"][i] // 2, top + d["top"][i] // 2, d["width"][i] // 2,
                            d["height"][i] // 2))
    _RESCAN[page.sha] = out
    return out


def find_words(page: Page, pattern: str, rescan: bool = False) -> list[Word]:
    rx = re.compile(pattern, re.I)
    hits = [w for w in page.words if rx.search(w.text)]
    if not hits and rescan:
        hits = [w for w in _rescan_lower_half(page) if rx.search(w.text)]
    return hits


SIGN_LABEL = r"^s\w{0,4}[gf]?atu\w*|^signa\w*|^sig(?:n)?$"


def _rulings(ink: np.ndarray) -> np.ndarray:
    """Long horizontal lines, solid or dotted (dots merged first). Signature lines, not pen strokes."""
    dil = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 1)))
    return cv2.morphologyEx(dil, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (120, 1)))


def signature_evidence(page: Page, label_pattern: str = SIGN_LABEL, layout: str = "auto") -> list[dict]:
    """For each 'Signature' label, measure pen strokes where a signature would be.

    Two layouts exist. Most SBD forms put the label *under* the signing line, so the signature is in the band
    above it. The supplier's quotation puts the label *before* a line, so the signature sits on that line, to
    the right. The layout is read from the page: a ruling to the right of the label means 'beside'. Forms that
    sign below the label (the sworn affidavit) are scanned with layout='around'.
    Rulings are removed first (an underline is not a signature), and words OCR reads with confidence
    (typed or neat handwritten names, dates, 'Director') are blanked so they are not mistaken for strokes."""
    gray = _gray(page)
    ink = ink_mask(gray)
    rule = _rulings(ink)
    ink = cv2.bitwise_and(ink, cv2.bitwise_not(cv2.dilate(rule, np.ones((7, 1), np.uint8))))
    for wd in page.words:
        if wd.conf >= 60:
            x0, y0, x1, y1 = wd.box
            ink[max(0, y0 - 2): y1 + 2, max(0, x0 - 2): x1 + 2] = 0
    H, W = ink.shape
    out = []
    for w in find_words(page, label_pattern, rescan=True):
        right = _rulings(ink_mask(gray))[max(0, w.top - 6): w.top + w.height + 16,
                                         min(W, w.left + w.width + 5): min(W, w.left + 520)]
        beside_layout = layout == "beside" or (layout == "auto" and right.sum() > 255 * 150)
        if layout == "above":
            beside_layout = False
        if layout == "around":
            zone = (max(0, w.left - 60), max(0, w.top - 190), min(W, w.left + 520), min(H, w.top + 190))
            sub = ink[zone[1]:zone[3], zone[0]:zone[2]]
            total = sum(a for _, _, cw, ch, a in _components(sub, 20) if ch >= 14 or a >= 150)
            full = zone
        elif beside_layout:
            line_y = int(w.top + w.height + 6)
            zone = (min(W, w.left + w.width + 5), max(0, w.top - 70), min(W, w.left + 520), min(H, w.top + 70))
            sub = ink[zone[1]:zone[3], zone[0]:zone[2]]
            # strokes of a signature reach the line; handwriting of the name above it does not
            total = sum(a for x, y, cw, ch, a in _components(sub, 20)
                        if (ch >= 14 or a >= 150) and zone[1] + y + ch >= line_y - 25)
            full = zone
        else:
            zone = (max(0, w.left - 60), max(0, w.top - 190), min(W, w.left + 520), max(0, w.top - 4))
            sub = ink[zone[1]:zone[3], zone[0]:zone[2]]
            total = sum(a for _, _, cw, ch, a in _components(sub, 20) if ch >= 14 or a >= 150)
            full = zone
        out.append({
            "label_box": w.box,
            "zone": full,
            "layout": layout if layout in ("around",) else ("beside" if beside_layout else "above"),
            "ink_px": int(total),
            "present": total >= 350,
            "confidence": "high" if total >= 900 or total < 100 else "medium",
        })
    return out


# --- leftover template highlighting -----------------------------------------

# form -> (index among that form's pages, label pattern, fallback anchor, where the signature sits).
# Layouts are fixed per form, not guessed from the page: the quotation and the local content declaration
# are signed ON a line to the right of the label, the SBD forms ABOVE the label, the affidavit around it.
SIGNATURE_SPEC = {
    "quote": (-1, r"^signature", None, "beside"),
    "sbd6_1": (-1, r"^signature\(s\)", r"^DATE:?$", "above"),
    "local_content_decl": (-1, r"^signature", None, "beside"),
    "sbd4": (-1, SIGN_LABEL, None, "above"),
    "sbd8": (-1, r"^signature", None, "above"),
    "sbd9": (-1, r"^signature", None, "above"),
    "bbbee_affidavit": (0, SIGN_LABEL, None, "around"),
}


def signature_zone(page: Page, dtype: str):
    """The box where this form's signature is measured, or None if the signature label is not on the page."""
    _, label, fallback, lay = SIGNATURE_SPEC[dtype]
    ev = signature_evidence(page, label, lay) or (signature_evidence(page, fallback, lay) if fallback else [])
    ev = sorted(ev, key=lambda e: e["label_box"][1])[-1:] if ev else []
    return (ev[0]["zone"], ev[0]["present"]) if ev else (None, False)


def highlight_remnants(page: Page, min_px: int = 4000) -> list[Region]:
    im = cv2.imread(page.image_path)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (20, 80, 150), (35, 255, 255))
    yellow = cv2.morphologyEx(yellow, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    regions = []
    for x, y, w, h, a in _components(yellow, 400):
        regions.append(Region((x, y, x + w, y + h), "highlight", int(a), "yellow highlight left from the template"))
    if sum(r.ink for r in regions) < min_px:
        return []
    return regions


# --- initials -------------------------------------------------------------

def initials_present(page: Page) -> bool:
    ink = ink_mask(_gray(page))
    h, w = ink.shape
    zone = ink[int(h * 0.86):, int(w * 0.70):]
    return sum(a for _, _, cw, ch, a in _components(zone, 15) if ch >= 15) >= 200


# --- stray marks ----------------------------------------------------------

def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def build_vocab(texts: list[str]) -> set[str]:
    vocab = set()
    for t in texts:
        for tok in re.split(r"\s+", t):
            n = _norm(tok)
            if len(n) >= 2:
                vocab.add(n)
    return vocab


_LABEL = re.compile(r"^\(?[a-z0-9]{1,2}\)?\.?$|^\d+(\.\d+)+\.?$|^\d+\.\d+\.\d+$", re.I)


_STRICT_LABEL = re.compile(r"^\(?[a-f]\)$|^\d{1,2}(\.\d{1,2}){0,3}\.?$")


def _ocr_label(page: Page, box) -> bool:
    """Re-read just the cluster. Printed paragraph numbers ('2.7.1', '(a)') come back clean;
    handwriting and stray scribbles do not."""
    import pytesseract
    from PIL import Image
    x0, y0, x1, y1 = box
    im = Image.open(page.image_path).convert("L").crop((max(0, x0 - 8), max(0, y0 - 8), x1 + 8, y1 + 8))
    im = im.resize((im.width * 2, im.height * 2))
    d = pytesseract.image_to_data(
        im, config="--psm 7 -c tessedit_char_whitelist=0123456789.()abcdef",
        output_type=pytesseract.Output.DICT)
    toks = [(t.strip(), float(c)) for t, c in zip(d["text"], d["conf"]) if t.strip()]
    if len(toks) != 1:
        return False
    t, c = toks[0]
    return c >= 60 and bool(_STRICT_LABEL.match(t))


def _near_yes_no(page: Page, box, pad: int = 70) -> bool:
    x0, y0, x1, y1 = box
    for wd in page.words:
        if re.fullmatch(r"(yes|no)(\s*/\s*(yes|no))?", wd.text.strip("|()[] "), re.I):
            a, b, c, d = wd.box
            if not (x1 < a - pad or x0 > c + pad or y1 < b - pad or y0 > d + pad):
                return True
    return False


def _is_printed_label(page: Page, box) -> bool:
    x0, y0, x1, y1 = box
    area = max(1, (x1 - x0) * (y1 - y0))
    for wd in page.words:
        if wd.conf < 30 or not _LABEL.match(wd.text):
            continue
        a, b, c, d = wd.box
        inter = max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
        word_area = max(1, (c - a) * (d - b))
        small = (x1 - x0) < 120 and (y1 - y0) < 60
        if inter >= 0.5 * area or (small and inter >= 0.8 * word_area):
            return True
    return False


def stray_marks(page: Page, vocab: set[str]) -> list[Region]:
    """Ink that is neither printed form text, nor a ruling/leader line, nor in the
    initials corner. Each cluster is classed by where it sits:
      margin   - outside the printed text block (the strongest 'unexpected mark' signal)
      overwrite- on top of printed template words (strike-through, overwriting)
      entry    - in the body, next to a printed label or line, presumed to be a filled-in field (not reported)
    """
    gray = _gray(page)
    ink = ink_mask(gray)
    H, W = ink.shape

    printed = np.zeros_like(ink)
    printed_boxes = []
    for wd in page.words:
        if wd.conf >= 60 and _norm(wd.text) in vocab:
            x0, y0, x1, y1 = wd.box
            printed[max(0, y0 - 3): y1 + 3, max(0, x0 - 3): x1 + 3] = 255
            printed_boxes.append(wd.box)

    # rulings: solid lines, plus dotted leaders (dilate so dots merge, then open)
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (70, 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 70)))
    dotted = cv2.morphologyEx(cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 1))),
                              cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (120, 1)))
    lines = cv2.dilate(cv2.bitwise_or(cv2.bitwise_or(horiz, vert), dotted),
                       cv2.getStructuringElement(cv2.MORPH_RECT, (3, 7)))

    rest = cv2.bitwise_and(ink, cv2.bitwise_not(cv2.bitwise_or(printed, lines)))
    rest = cv2.morphologyEx(rest, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    # text block extents from printed words (robust 2nd / 98th percentile)
    if printed_boxes:
        xs0 = np.array([b[0] for b in printed_boxes]); xs1 = np.array([b[2] for b in printed_boxes])
        left_edge, right_edge = np.percentile(xs0, 2), np.percentile(xs1, 98)
    else:
        left_edge, right_edge = W * 0.08, W * 0.92

    merged = cv2.dilate(rest, cv2.getStructuringElement(cv2.MORPH_RECT, (31, 17)))
    out: list[Region] = []
    for x, y, w, h, _ in _components(merged, 1):
        inkpx = int((rest[y:y + h, x:x + w] > 0).sum())
        if inkpx < 60:
            continue
        cx, cy = x + w / 2, y + h / 2
        if cx > W * 0.72 and cy > H * 0.90:
            continue  # initials corner
        # page numbers / footers (tiny, near bottom or top edge)
        if (cy > H * 0.93 or cy < H * 0.03) and w < 120 and h < 80:
            continue
        pad = 6
        over = any(not (x + w < b[0] - pad or x > b[2] + pad or y + h < b[1] - pad or y > b[3] + pad)
                   and (min(x + w, b[2]) - max(x, b[0])) * (min(y + h, b[3]) - max(y, b[1])) > 0.5 * (b[2] - b[0]) * (b[3] - b[1])
                   for b in printed_boxes)
        outside_block = (x + w) < left_edge - 25 or x > right_edge + 25
        if outside_block and w / max(h, 1) > 3 and h < 30:
            continue  # ruling fragment from a skewed scan, not a mark
        if outside_block and (_is_printed_label(page, (x, y, x + w, y + h))
                              or _near_yes_no(page, (x, y, x + w, y + h))
                              or _ocr_label(page, (x, y, x + w, y + h))):
            continue  # printed paragraph numbers, or a circled YES/NO answer
        if outside_block:
            kind, note = "margin", "ink outside the printed text block"
        elif over and (w > 90 and h < 60) is False and inkpx > 400 and w > 150:
            kind, note = "overwrite", "ink crossing printed text"
        else:
            kind, note = "entry", "presumed filled-in field"
        out.append(Region((x, y, x + w, y + h), kind, inkpx, note))
    return out


# --- page cut off at an edge: non-AI first, the model only if this can't decide --------

def page_cutoff(page: Page, siblings: list[Page]) -> bool | None:
    """True/False when this can be decided without a model: the page is noticeably shorter than the other
    pages in the same pack (a scan fed in short), or confidently-read text sits flush against an edge (the
    scanner bed clipped it). None means neither signal fired either way - ask the vision model instead."""
    from PIL import Image
    w, h = Image.open(page.image_path).size
    heights = sorted(Image.open(p.image_path).size[1] for p in siblings if p.doc_type != "blank")
    if len(heights) >= 3:
        median_h = heights[len(heights) // 2]
        if h < median_h * 0.85:
            return True
    margin = 6
    if any(wd.conf >= 60 and (wd.left <= margin or wd.top <= margin or wd.left + wd.width >= w - margin
                              or wd.top + wd.height >= h - margin) for wd in page.words):
        return True
    return None


# --- second look at a disputed value -------------------------------------------

def reread(page: Page, box, whitelist: str = "0123456789/()", scale: int = 3, pad: int = 10) -> str:
    """Re-OCR one value at higher magnification with a restricted alphabet. Used when two readings of
    the same number differ by a character or two: it separates a scanner misread from a real change."""
    import pytesseract
    from PIL import Image
    x0, y0, x1, y1 = box
    im = Image.open(page.image_path).convert("L").crop((max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad))
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    reads = []
    for sc in (scale, scale + 1):
        big = im if sc == scale else im.resize((im.width // scale * sc, im.height // scale * sc), Image.LANCZOS)
        t = pytesseract.image_to_string(big, config=f"--psm 7 -c tessedit_char_whitelist={whitelist}")
        reads.append(re.sub(r"\s", "", t))
    return reads[0] if reads[0] == reads[1] else ""  # two magnifications must agree, else no opinion
