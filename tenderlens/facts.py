"""Step 2 - turn the response pack into facts, each tagged with where it came from.

Sources, weakest to strongest trust is NOT assumed - the audit shows the source on
every finding so a reviewer knows what to double-check:
  ocr     Tesseract over the scan (reliable for printed text, poor for handwriting)
  vision  multimodal Llama reading the page image (handwriting, ticks, circled answers)
  manual  a human transcription file (used when no vision model is attached)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import forms as F
from .ingest import Page
from .llm import LlamaClient, LLMUnavailable


@dataclass
class Fact:
    field: str
    value: object
    page: int
    source: str  # ocr | vision | manual
    confidence: float = 1.0
    box: tuple | None = None
    verified: bool = False  # a vision reading that a second, differently worded question confirmed
    note: str = ""


class Facts:
    def __init__(self):
        self.items: list[Fact] = []
        self.unread: list[tuple[str, int, str]] = []  # (field, page, why) entries a reader could not read
        self.vision_log: dict = {"calls": 0, "failed": 0, "pages_read": 0, "pages_inspected": 0, "errors": []}

    def add(self, *a, **k):
        f = Fact(*a, **k)
        self.items.append(f)
        return f

    def all(self, field: str) -> list[Fact]:
        return [f for f in self.items if f.field == field]

    def best(self, field: str) -> Fact | None:
        fs = self.all(field)
        if not fs:
            return None
        # a person's transcription outranks a model's reading, which outranks OCR; then confidence
        rank = {"manual": 3, "vision": 2, "ocr": 1}
        return sorted(fs, key=lambda f: (rank.get(f.source, 0), f.confidence), reverse=True)[0]

    def value(self, field: str, default=None):
        f = self.best(field)
        return f.value if f else default


# ---------------------------------------------------------------------------
# OCR facts (printed text only)
# ---------------------------------------------------------------------------

def locate(page: Page, fragment: str) -> tuple | None:
    frag = re.sub(r"\W", "", fragment).lower()
    if len(frag) < 3:
        return None
    for w in page.words:
        if frag[:6] in re.sub(r"\W", "", w.text).lower():
            return w.box
    return None


def locate_value(page: Page, value: str, max_words: int = 6) -> tuple | None:
    """Box around a (possibly multi-word) value, found by joining consecutive OCR words until they spell it."""
    target = re.sub(r"[\s\\]", "", value).replace("\\", "/").lower()
    ws = page.words
    for i in range(len(ws)):
        joined = ""
        for j in range(i, min(len(ws), i + max_words)):
            joined += ws[j].text.replace("\\", "/").lower()
            if joined == target or (len(joined) >= len(target) and target in joined and j == i):
                grp = ws[i:j + 1]
                if max(w.top for w in grp) - min(w.top for w in grp) > 30:
                    break
                return (min(w.left for w in grp), min(w.top for w in grp),
                        max(w.left + w.width for w in grp), max(w.top + w.height for w in grp))
            if len(joined) > len(target) + 2:
                break
    return None


def _union(ws):
    return (min(w.left for w in ws), min(w.top for w in ws), max(w.left + w.width for w in ws),
            max(w.top + w.height for w in ws))


def value_right_of(page: Page, label: str, max_words: int = 4, first_gap: int = 350, max_gap: int = 45,
                   stop: tuple = ()) -> str:
    """The words on the same row, to the right of a label (e.g. 'Enterprise Status'), read from the word boxes. Safer than
    a text regex when OCR reorders lines: the value must actually sit beside its label. The label is matched along the
    row, so it does not matter where in OCR's reading order its words came."""
    toks = label.split()
    for w0 in page.words:
        if not re.fullmatch(re.escape(toks[0]), w0.text, re.I):
            continue
        cy = w0.top + w0.height / 2
        row = sorted((w for w in page.words if abs(w.top + w.height / 2 - cy) < max(12, w0.height * 0.7)),
                     key=lambda w: w.left)
        i = row.index(w0)
        if not all(i + k < len(row) and re.fullmatch(re.escape(t), row[i + k].text, re.I) for k, t in enumerate(toks)):
            continue
        lab = row[i + len(toks) - 1]
        out, right = [], lab.left + lab.width
        for w in row[i + len(toks):]:
            if w.left - right > (max_gap if out else first_gap) or len(out) >= max_words or w.text in stop:
                break
            out.append(w.text)
            right = w.left + w.width
        return " ".join(out)
    return ""


def quote_boxes(page: Page) -> dict:
    """Word boxes for the figures on the quotation, so a report can show the reader the exact spot."""
    ws, out = page.words, {}
    same_line = lambda a, b: abs((a.top + a.height / 2) - (b.top + b.height / 2)) < 14
    tot = [i for i, w in enumerate(ws) if re.fullmatch(r"TOTAL:?", w.text)]
    if tot:
        i = tot[-1]
        grp = [w for w in ws[i + 1:i + 4] if same_line(w, ws[i]) and re.match(r"R?\d", w.text)]
        if grp:
            out["total"] = _union(grp)
    unit = next((w for w in ws if re.fullmatch(r"R\d[\d ]*\.\d+", w.text)), None)
    if unit:
        out["unit_price"] = unit.box
        q = [w for w in ws if re.fullmatch(r"\d{1,4}", w.text) and same_line(w, unit) and w.left < unit.left]
        if q:
            out["quantity"] = q[-1].box
    for i, w in enumerate(ws):
        if w.text == "days" and i and re.fullmatch(r"\d+", ws[i - 1].text) and i > 2 and ws[i - 2].text == "for":
            out["validity"] = _union(ws[i - 1:i + 1])
    return out


ID_PATTERNS = {
    "id.reg_no": r"(?<!\d)\d{4}\s?[/\\]\s?\d{5,6}\s?[/\\]\s?\d{2}(?!\d)",
    "id.csd_no": r"MAAA\d{5,8}",
    "id.email": r"[\w.\-]+@[\w.\-]+\.[a-z]{2,}",
}
# Context-bound patterns: a bare 10- or 13-digit number is too ambiguous (bank account numbers,
# SARS request references and vendor numbers look exactly like tax and ID numbers).
TAX_CONTEXT = r"(?:Tax(?:payer)?\s*(?:Reference)?\s*Number(?:\(s\))?[:\s]*(?:IT\s*-\s*)?)(\d{10})(?!\d)"
ID_CONTEXT_PAGES = {"cipc", "bbbee_affidavit"}


def plausible_sa_id(v: str) -> bool:
    """YYMMDD + 4-digit sequence + citizenship (0/1) + 8/9 + check digit."""
    if not re.fullmatch(r"\d{13}", v):
        return False
    mm, dd = int(v[2:4]), int(v[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31 and v[10] in "01" and v[11] in "89"


def ocr_facts(pages: list[Page], facts: Facts) -> None:
    for p in pages:
        for field, pat in ID_PATTERNS.items():
            for m in re.finditer(pat, p.text, re.I):
                v = re.sub(r"\s", "", m.group(0)).replace("\\", "/")
                if field == "id.email" and v.lower().endswith("gov.za"):
                    continue  # buyer addresses
                facts.add(field, v, p.number, "ocr", p.mean_conf / 100, locate_value(p, v) or locate(p, v[:8]))
        for m in re.finditer(TAX_CONTEXT, p.text, re.I):
            facts.add("id.tax_no", m.group(1), p.number, "ocr", p.mean_conf / 100, locate(p, m.group(1)[:6]))
        if p.doc_type in ID_CONTEXT_PAGES:
            for m in re.finditer(r"(?<!\d)\d{13}(?!\d)", p.text):
                if plausible_sa_id(m.group(0)):
                    facts.add("id.sa_id", m.group(0), p.number, "ocr", p.mean_conf / 100, locate(p, m.group(0)[:6]))
        facts.add("page.doc_type", p.doc_type, p.number, "ocr", 1.0)

    def by(dtype):
        return [p for p in pages if p.doc_type == dtype]

# quote page
    for p in by("quote"):
        t = p.text
        qb = quote_boxes(p)
        m = re.search(r"FILE\s*NO\s*:?\s*([\d/()]+\d{2}/\d{2})", t, re.I)
        if m:
            facts.add("quote.reference", m.group(1), p.number, "ocr", p.mean_conf / 100,
                      locate_value(p, m.group(1)) or locate(p, "FILE"))
        m = re.search(r"R\s?(\d[\d\s]*\.\d)\b", t)
        if m:
            facts.add("quote.unit_price", float(m.group(1).replace(" ", "")), p.number, "ocr", 0.9, qb.get("unit_price"))
        m = re.search(r"(?<!\d)(\d{1,4})\s+R\s?\d[\d\s]*\.\d", t)
        if m:
            facts.add("quote.quantity", int(m.group(1)), p.number, "ocr", 0.9, qb.get("quantity"))
        m = re.search(r"TOTAL:\s*R\s?(\d[\d\s]*\d)", t)
        if m:
            facts.add("quote.total", float(m.group(1).replace(" ", "")), p.number, "ocr", 0.9, qb.get("total"))
        m = re.search(r"valid for (\d+) days", t, re.I)
        if m:
            facts.add("quote.validity_days", int(m.group(1)), p.number, "ocr", 0.9, qb.get("validity"))
        if re.search(r"not VAT registered", t, re.I):
            facts.add("quote.vat_registered", False, p.number, "ocr", 0.9)
        m = re.search(r"Closing date\W+(\d{4}/\d{2}/\d{2})", t, re.I)
        if m:
            facts.add("quote.closing_date", m.group(1).replace("/", "-"), p.number, "ocr", 0.9)
        m = re.search(r"Quotation date:\s*(\d{4}/\d{2}/\d{2})", t, re.I)
        if m:
            facts.add("quote.date_typed", m.group(1).replace("/", "-"), p.number, "ocr", 0.9)
        m = re.search(r"\bTo:(.*)", t, re.S)  # the buyer's address is the first Email after 'To:'
        if m:
            e = re.search(r"Email:\s*([\w.\-]+@[\w.\-]+)", m.group(1), re.I)
            if e:
                facts.add("quote.addressed_to_email", e.group(1), p.number, "ocr", 0.9)
        m = re.search(r"Delivery address:\s*(.+?)\s*Quote reference", t, re.I | re.S)
        if m:
            facts.add("quote.delivery_address", re.sub(r"\s+", " ", m.group(1)).strip(), p.number, "ocr", 0.9)

    # local content declaration price (printed digits in a typed-looking box, handwritten here)
    for p in by("csd"):
        t = p.text
        m = re.search(r"Certificate Expiry Date\s+(\d{1,2} \w{3} \d{4})", t)
        if m:
            facts.add("csd.bbbee_expiry", m.group(1), p.number, "ocr", 0.9)
        m = re.search(r"Certificate Issue Date\s+(\d{1,2} \w{3} \d{4})", t)
        if m:
            facts.add("csd.bbbee_issue", m.group(1), p.number, "ocr", 0.9)
        if re.search(r"\bStatus\s+Expired", t):
            facts.add("csd.bbbee_status", "Expired", p.number, "ocr", 0.9)
        if re.search(r"Manual verification required", t):
            facts.add("csd.bbbee_verification", "Manual verification required", p.number, "ocr", 0.9)
        m = re.search(r"Restricted Supplier\W+(Yes|No)\b", t)
        if m:
            facts.add("csd.restricted", m.group(1).title(), p.number, "ocr", 0.8)
        if re.search(r"VAT vendor\?\s*No", t):
            facts.add("csd.vat_vendor", "No", p.number, "ocr", 0.8)
        v = value_right_of(p, "Overall Tax Status", 4, 260, stop=("is", "Is"))
        if "compliant" not in v.lower():   # a status is only recorded when it actually names one
            m = re.search(r"Overall Tax Status[ \t]+(.{3,40}?)(?:\s+[Ii]s this|\n|$)", t)
            v = m.group(1).strip() if m else v
        if "compliant" in v.lower():
            facts.add("csd.tax_status", "Tax Compliant" if re.fullmatch(r"Tax\s+Compliant", v.strip(), re.I) else v.strip(),
                      p.number, "ocr", 0.8)
        m = re.search(r"Business status\s+([A-Za-z][A-Za-z ]{2,30})", t)
        if m:
            facts.add("csd.business_status", m.group(1).strip(), p.number, "ocr", 0.8)
        m = re.search(r"Is supplier active\?\s*(Yes|No)\b", t)
        if m:
            facts.add("csd.active", m.group(1).title(), p.number, "ocr", 0.8)
        m = re.search(r"Legal name\s+(.+?)\s+Restricted", t)
        if m:
            facts.add("csd.legal_name", m.group(1).strip(), p.number, "ocr", 0.8)
        if re.search(r"Batteries", t):
            facts.add("csd.commodity_batteries", True, p.number, "ocr", 0.9)
        m = re.search(r"Report Date:?\s*\n?\s*(\d{1,2} \w{3} \d{4})", t)
        if m:
            facts.add("csd.report_date", m.group(1), p.number, "ocr", 0.9)

    LEVELS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
    for p in by("bbbee_affidavit"):
        t = p.text
        m = re.search(r"enterprise is (\d{1,3})\s?%\s*black\s*(?:male\s*)?owned", t, re.I)
        if m:
            facts.add("bbbee_affidavit.black_ownership_pct", int(m.group(1)), p.number, "ocr", 0.9)
        m = re.search(r"Level\s+(One|Two|Three|Four|Five|Six|Seven|Eight|\d)\b", t, re.I)
        if m:
            g = m.group(1).lower()
            facts.add("bbbee_affidavit.level", LEVELS.get(g) or int(g), p.number, "ocr", 0.9)
        m = re.search(r"(\d{2,3})\s?%\s*B-?BBEE\s+procurement\s+recognition", t, re.I)
        if m:
            facts.add("bbbee_affidavit.recognition_pct", int(m.group(1)), p.number, "ocr", 0.8)
        if re.search(r"did not exceed R\s?10[,.\s]?000[,.\s]?000", t):
            facts.add("bbbee_affidavit.eme", True, p.number, "ocr", 0.9)
        elif re.search(r"income\s+(?:did\s+)?exceed", t, re.I):
            facts.add("bbbee_affidavit.eme", False, p.number, "ocr", 0.8)
        if re.search(r"valid for a period of 12 months", t, re.I):
            facts.add("bbbee_affidavit.validity_clause", True, p.number, "ocr", 0.8)
        m = re.search(r"Full name\s*&\s*Surname:\s*(.+)", t)
        if m:
            facts.add("bbbee_affidavit.deponent", m.group(1).strip(), p.number, "ocr", 0.9)
        m = re.search(r"Enterprise Name:\s*(.+)", t)
        if m:
            facts.add("bbbee_affidavit.enterprise_name", m.group(1).strip(), p.number, "ocr", 0.9)

    for p in by("cipc"):
        m = re.search(r"Registration Date\s+(\d{2}/\d{2}/\d{4})", p.text)
        if m:
            facts.add("cipc.registration_date", m.group(1), p.number, "ocr", 0.9)
        m = re.search(r"Enterprise Name\s+(.+)", p.text)
        if m:
            facts.add("cipc.enterprise_name", m.group(1).strip(), p.number, "ocr", 0.9)
        m = re.search(r"([A-Z][A-Z'\-]+,\s*[A-Z][A-Z ]+?)\s+(?:Director|Member)\b", p.text)
        if m:
            facts.add("cipc.director", m.group(1).strip(), p.number, "ocr", 0.9)
        for key, label in (("status", "Enterprise Status"), ("type", "Enterprise Type")):
            v = value_right_of(p, label)
            if v:
                facts.add(f"cipc.{key}", v, p.number, "ocr", 0.9)

    for p in by("tcs"):
        m = re.search(r"Taxpayer Name\s+(.+)", p.text)
        if m:
            facts.add("tcs.taxpayer_name", m.group(1).strip(), p.number, "ocr", 0.9)
        m = re.search(r"Issue Date:?\s*(\d{4})/(\d{2})/(\d{2})", p.text)
        if m:
            facts.add("tcs.issue_date", "-".join(m.groups()), p.number, "ocr", 0.9)
        m = re.search(r"Purpose of Request\s+(\w+)", p.text)
        if m:
            facts.add("tcs.purpose", m.group(1), p.number, "ocr", 0.9)
        m = re.search(r"PIN Expiry Date\s+(\d{2}/\d{2}/\d{4})", p.text)
        if m:  # OCR often garbles this one - the audit falls back to manual/vision and says so
            d, mo, y = m.group(1).split("/")
            facts.add("tcs.pin_expiry", f"{y}-{mo}-{d}", p.number, "ocr", 0.9)


# ---------------------------------------------------------------------------
# manual transcript / vision
# ---------------------------------------------------------------------------

ENTRY = {e.key: e for es in F.FORMS.values() for e in es}


def _record(facts: Facts, key: str, raw, page: int, source: str, conf: float = 1.0, verified: bool = False):
    """Clean a reading with the rules for that field and store it. Returns the Fact (or None if nothing to store)."""
    e = ENTRY.get(key)
    kind = e.kind if e else (F.BOOL if key.startswith("inspect.") else None)
    val = F.clean(kind, raw) if kind else raw
    if val is F.NOT_ON_PAGE:
        return None
    if val is F.UNREAD:
        facts.unread.append((key, page, f"{source} could not read it"))
        return None
    return facts.add(key, val, page, source, conf, verified=verified)


def load_transcript(path: Path, facts: Facts, pages: list[Page] | None = None) -> dict:
    """Apply a human (or test-script) transcription of the handwriting. Each field is tied to the hash of the page
    image it was read from, so it follows that page to its new position if pages were removed or reordered, and is
    NOT applied when that page is gone or has been altered (a transcription of a different image proves nothing).
    A value of "blank" records that the field is present and empty; "unreadable" records that it could not be read.
    Returns {'applied': n, 'unmatched': {original_page: n_fields}}."""
    data = json.loads(Path(path).read_text())
    by_sha = {p.sha: p.number for p in pages} if pages else {}
    applied, unmatched = 0, {}
    for it in data["fields"]:
        if pages and it.get("page_sha"):
            page = by_sha.get(it["page_sha"])
            if page is None:
                unmatched[it["page"]] = unmatched.get(it["page"], 0) + 1
                continue
        else:
            page = it["page"]
        _record(facts, it["field"], it["value"], page, "manual", it.get("confidence", 1.0),
                verified=bool(it.get("verified", True)))
        applied += 1
    return {"applied": applied, "unmatched": unmatched}


# --- vision (multimodal Llama) ---------------------------------------------------------------------

VISION_SYSTEM = (
    "You read scanned South African government tender forms. Look ONLY at the page image you are given. "
    "Report what is actually written, ticked or stamped on the page. Never guess a value that is not visible, "
    "and never copy the printed form text as if it were an answer. Return ONLY a JSON object."
)


def _ask(llm: LlamaClient, facts: Facts, prompt: str, images: list[str]):
    facts.vision_log["calls"] += 1
    try:
        return llm.complete_json(VISION_SYSTEM, prompt, images=images)
    except (LLMUnavailable, OSError, ValueError, KeyError, IndexError) as err:
        facts.vision_log["failed"] += 1
        msg = f"{type(err).__name__}: {str(err)[:140]}"
        if msg not in facts.vision_log["errors"] and len(facts.vision_log["errors"]) < 4:
            facts.vision_log["errors"].append(msg)
        return None


def _answer(data, key="answer"):
    if not isinstance(data, dict):
        return None
    v = data.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _second_look(llm: LlamaClient, facts: Facts, page: Page, entry: F.Entry, fact: Fact) -> None:
    """A reading that would raise an issue (blank, Yes, both boxes, an impossible date) is asked again as a single,
    differently worded question. Agreement makes it a verified reading; disagreement leaves it as an observation."""
    prompt = (f"This is page {page.number} of a bid pack. Question [{entry.key}]: {entry.desc}. "
              "Answer with JSON {\"answer\": \"...\"}. For a yes/no question answer exactly Yes, No, Both (both "
              "boxes marked) or blank (neither marked). For anything else write exactly what is written, or "
              "blank if the field is on this page and empty, or "
              "'not on this page' if it is not on this page.")
    data = _ask(llm, facts, prompt, [page.image_path])
    if data is None:
        fact.confidence, fact.note = 0.5, "the second look could not be made"
        return
    again = F.clean(entry.kind, _answer(data))
    if again == fact.value and again is not F.NOT_ON_PAGE and again is not F.UNREAD:
        fact.verified, fact.confidence = True, 0.9
    else:
        fact.confidence = 0.4
        fact.note = "a second, differently worded look read this differently"


def vision_extract(llm: LlamaClient, pages: list[Page], facts: Facts, local_content: bool = True, verify: bool = True):
    """Read every handwritten entry in forms.FORMS. One call per PAGE (small local models cope badly with several
    images and long answers), then merge: a value on any page beats a 'blank' on another, because each entry
    lives on one page only. Any reading that would raise an issue gets a second look (see _second_look)."""
    from collections import Counter
    for dtype, entries in F.FORMS.items():
        entries = [e for e in entries if local_content or not e.applies]
        pgs = [p for p in pages if p.doc_type == dtype]
        if not pgs or not entries:
            continue
        reads: dict[str, list] = {}
        fields = {e.key: f"[{e.kind}] {e.desc}" for e in entries}
        for p in pgs:
            prompt = (
                f"This image is page {p.number} of a bid pack and belongs to the form '{dtype}'. For EACH field "
                "listed, write what is handwritten, ticked or stamped on THIS page. Use \"not on this page\" if the "
                "field is not on this page, \"blank\" if it is on this page but nothing is written in it, \"unreadable\" if you "
                "cannot read it. For yes/no questions answer exactly Yes, No, Both (both boxes marked) or blank. "
                "Dates: copy them as written. Return JSON of the form {\"<field>\": \"<answer>\"}.\nFields:\n"
                + json.dumps(fields, indent=1))
            data = _ask(llm, facts, prompt, [p.image_path])
            if not isinstance(data, dict):
                continue
            facts.vision_log["pages_read"] += 1
            for e in entries:
                raw = data.get(e.key)
                raw = raw.get("value") if isinstance(raw, dict) else raw
                c = F.clean(e.kind, raw)
                if c is F.NOT_ON_PAGE:
                    continue
                if c is F.UNREAD:
                    facts.unread.append((e.key, p.number, "the model could not read it"))
                    continue
                reads.setdefault(e.key, []).append((c, p.number))
        for e in entries:
            got = reads.get(e.key)
            if not got:
                continue
            values = [(c, pg) for c, pg in got if c is not F.BLANK]
            if values:
                top = Counter(c for c, _ in values).most_common(1)[0][0]
                page_no = next(pg for c, pg in values if c == top)
                fact = facts.add(e.key, top, page_no, "vision", 0.6)
            else:
                page_no = pgs[-1].number if e.where == "last" else got[0][1]
                fact = facts.add(e.key, F.BLANK, page_no, "vision", 0.6)
            if verify and F.is_deviation(e, fact.value):
                _second_look(llm, facts, next(p for p in pgs if p.number == fact.page), e, fact)


def inspect_pages(llm: LlamaClient, pages: list[Page], facts: Facts, passes: int = 2) -> None:
    """Ask a vision model what no rule can see: crossed-out print, correction fluid, notes in the margin, pages
    that are rotated or cut off, scans too poor to read, and whether the signing line holds a real pen signature.
    A 'yes' is a lead. With passes=2 it is repeated as a differently worded question and only a second 'yes'
    makes it a verified reading."""
    signing = {}
    for dtype, idx in F.SIGNING_PAGE.items():
        pgs = [p for p in pages if p.doc_type == dtype]
        if pgs:
            signing[pgs[idx].number] = dtype
    for p in pages:
        if p.doc_type not in F.INSPECTION_FORMS:
            continue
        qs = {k: v[0] for k, v in F.INSPECTIONS.items()}
        if p.number in signing:
            qs["signature_seen"] = ("Is there a handwritten pen signature (not a typed name, and not only a printed "
                                    "line) beside or above the word 'Signature'?")
        prompt = (f"This image is page {p.number} of a bid pack (form '{p.doc_type}'). Answer each question with "
                  "exactly yes or no. Say yes only if you can clearly see it. Return JSON {\"<key>\": \"yes\" or "
                  "\"no\"}.\n" + json.dumps(qs, indent=1))
        data = _ask(llm, facts, prompt, [p.image_path])
        if not isinstance(data, dict):
            continue
        facts.vision_log["pages_inspected"] += 1
        for key in qs:
            ans = F.clean(F.BOOL, data.get(key))
            if key == "signature_seen":
                lead, second_q = ans is False, "Is the signing line empty, with no pen signature on it?"
                field = "inspect.signature_missing"
            else:
                lead, second_q = ans is True, F.INSPECTIONS[key][1]
                field = f"inspect.{key}"
            if not lead:
                continue
            fact = facts.add(field, True, p.number, "vision", 0.6)
            if passes >= 2:
                again = F.clean(F.BOOL, _answer(_ask(llm, facts, f"Page {p.number}. {second_q} Answer JSON "
                                                     "{\"answer\": \"yes\" or \"no\"}.", [p.image_path])))
                if again is True:
                    fact.verified, fact.confidence = True, 0.9
                else:
                    fact.confidence, fact.note = 0.4, "a second, differently worded look did not confirm it"


def signature_match(llm: LlamaClient, pages: list[Page], facts: Facts) -> None:
    """Compare each signing page's signature with the quotation's, as one picture with the two stacked (A over B).
    Handwriting similarity is a lead for a person, never proof, so the audit only ever reports it as an observation."""
    from PIL import Image, ImageDraw
    from . import vision as V
    crops = {}
    for dtype, (idx, *_rest) in V.SIGNATURE_SPEC.items():
        pgs = [p for p in pages if p.doc_type == dtype]
        if not pgs:
            continue
        p = pgs[idx]
        zone, present = V.signature_zone(p, dtype)
        if zone and present:
            crops[dtype] = (p, Image.open(p.image_path).convert("RGB").crop(zone))
    ref = "quote" if "quote" in crops else next(iter(crops), None)
    if not ref:
        return

    def stacked(a, b, path):
        w = 700
        parts = []
        for tag, im in (("A", a), ("B", b)):
            im = im.resize((w, max(60, int(im.height * w / im.width))))
            canvas = Image.new("RGB", (w, im.height + 26), "white")
            canvas.paste(im, (0, 26))
            ImageDraw.Draw(canvas).text((6, 6), tag, fill=(0, 0, 0))
            parts.append(canvas)
        out = Image.new("RGB", (w, sum(x.height for x in parts) + 10), (200, 200, 200))
        y = 0
        for x in parts:
            out.paste(x, (0, y))
            y += x.height + 10
        out.save(path)
        return str(path)

    ask = ("The image shows two handwritten signatures, A on top and B below. Ignore any printed words. Were they "
           "written by the same person? Return JSON {\"answer\": \"same\", \"different\" or \"unsure\"}.")
    for dtype, (p, im) in crops.items():
        if dtype == ref:
            continue
        base = Path(p.image_path).parent
        ab = _answer(_ask(llm, facts, ask, [stacked(crops[ref][1], im, base / f"sig-{ref}-{dtype}.png")]))
        ba = _answer(_ask(llm, facts, ask, [stacked(im, crops[ref][1], base / f"sig-{dtype}-{ref}.png")]))
        a, b = str(ab or "").lower(), str(ba or "").lower()
        if a.startswith("diff") and b.startswith("diff"):
            f = facts.add(f"sigmatch.{dtype}", "different", p.number, "vision", 0.9, verified=True)
        elif a.startswith("diff") or b.startswith("diff"):
            f = facts.add(f"sigmatch.{dtype}", "different", p.number, "vision", 0.4,
                          note="the two orderings of the pair disagreed")
        else:
            continue
        f.note = f.note or f"compared with the {ref} signature"
