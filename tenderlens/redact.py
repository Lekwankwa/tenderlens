"""Redaction for anything that leaves your machine: reports, screenshots, demo videos.

Analysis always runs on the original scans. Redaction only affects the images embedded in the
report (and any export). Three layers, because no single one catches everything:
  1. OCR text patterns   - typed numbers and emails, mapped back to word boxes
  2. Anchored zones      - handwritten fields OCR cannot read (ID, tax number, director table)
  3. Signature zones     - the band above every 'Signature' label
"""
from __future__ import annotations

import re

from PIL import Image, ImageDraw

from .ingest import Page, Word

PATTERNS = {
    "sa_id": r"(?<!\d)\d{13}(?!\d)",
    "ten_digit": r"(?<!\d)\d{10}(?!\d)",       # tax numbers, bank account numbers, vendor numbers
    "phone": r"(?<!\d)0\d{2}\s?\d{3}\s?\d{4}(?!\d)",
    "email": r"[\w.\-]+@(?!(?:[\w.\-]*gov\.za))[\w.\-]+\.[a-z]{2,}",
}
STRICT_EXTRA = {
    "reg_no": r"(?<!\d)\d{4}\s?[/\\]\s?\d{5,6}\s?[/\\]\s?\d{2}(?!\d)",
    "csd_no": r"MAAA\d{5,8}",
}


def _joined(words: list[Word]):
    text, spans = "", []
    for w in words:
        start = len(text)
        text += w.text + " "
        spans.append((start, start + len(w.text)))
    return text, spans


def text_boxes(page: Page, strict: bool) -> list[tuple]:
    pats = {**PATTERNS, **(STRICT_EXTRA if strict else {})}
    text, spans = _joined(page.words)
    boxes = []
    for rx in pats.values():
        for m in re.finditer(rx, text, re.I):
            a, b = m.span()
            for w, (s, e) in zip(page.words, spans):
                if s < b and e > a:
                    boxes.append(w.box)
    # SARS PIN: the token(s) after the word 'PIN' on the same line, TCS page only
    if page.doc_type == "tcs":
        for w in page.words:
            if w.text == "PIN" and w.conf > 80:
                for n in page.words:
                    if n.left > w.left + w.width and abs(n.top - w.top) < 25 and n.text.upper() not in (
                            "ISSUED", "EXPIRY", "DATE", "ONLY", "AT", "ALLOWS", "THE") and len(n.text) >= 4:
                        boxes.append(n.box)
    return boxes


def anchored_zones(page: Page) -> list[tuple]:
    zones = []
    if page.doc_type == "sbd4":
        # Section 2.2 (ID) and 2.5 (tax number) are handwritten and OCR cannot read them, but the printed
        # right-hand 'Number:' labels are found reliably: rows 1 and 3 of those labels are the ID and tax rows.
        nums = sorted([w for w in page.words if w.text == "Number:" and w.left > 1000 and 800 < w.top < 1300],
                      key=lambda w: w.top)
        if len(nums) >= 3:
            for w in (nums[0], nums[2]):
                zones.append((190, w.top - 15, w.left - 10, w.top + 95))
        # Director table (section 3): ID, personal tax and persal columns.
        ident = [w for w in page.words if w.text == "Identity" and w.left > 400 and w.top > 900]
        if ident:
            zones.append((ident[0].left - 20, ident[0].top + 70, page_width(page) - 60, ident[0].top + 520))
    return zones


def page_width(page: Page) -> int:
    return Image.open(page.image_path).width


def signature_zones(page: Page) -> list[tuple]:
    zones = []
    for w in page.words:
        if re.match(r"^signature", w.text, re.I) or (page.doc_type == "sbd4" and re.match(r"^s\w{0,4}[gf]?atu", w.text, re.I)
                                                       and w.top > 500):
            zones.append((max(0, w.left - 60), max(0, w.top - 190), w.left + 520, w.top + 70))
    if page.doc_type == "sbd6_1":  # label lost to OCR on the last page: anchor on DATE:
        for w in page.words:
            if re.fullmatch(r"DATE:?", w.text) and w.left > 500:
                zones.append((w.left - 60, w.top - 190, w.left + 520, w.top))
    return zones


def redact_image(page: Page, strict: bool = False, mask_signatures: bool = True) -> tuple[Image.Image, list[tuple]]:
    im = Image.open(page.image_path).convert("RGB")
    boxes = text_boxes(page, strict) + anchored_zones(page) + (signature_zones(page) if mask_signatures else [])
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in boxes:
        d.rectangle((x0 - 4, y0 - 4, x1 + 4, y1 + 4), fill=(20, 20, 20))
    return im, boxes
