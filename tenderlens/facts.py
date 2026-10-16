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
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import forms as F
from .ingest import Page
from .llm import LlamaClient, LLMError, LLMUnavailable, run_concurrent, vision_concurrency


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
        self.vision_log: dict = {"calls": 0, "failed": 0, "pages_read": 0, "pages_inspected": 0, "errors": [],
                                 "stopped_early": ""}
        self.misread_variants: list[str] = []  # handwriting a focused crop re-read cleared or set aside (Step 3B)
        self.stage_times: dict[str, float] = {}  # seconds per pipeline stage; filled in by cli.audit_pack
        self._lock = threading.Lock()  # protects vision_log, touched from worker threads by _ask (see below)

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


def label_box(page: Page, label: str, side: str = "right", w: int = 440, h: int = 55) -> tuple | None:
    """A box near a printed label (found the same way value_right_of finds one: consecutive words on one
    OCR row), sized to hold a short handwritten answer. side='right': the box starts just past the label,
    on the same row. side='below': the box starts under the label's row (for a heading like 'WITNESSES'
    with the answer written below it, not beside it). None if the label is not on this page."""
    toks = label.split()
    for w0 in page.words:
        if not re.fullmatch(re.escape(toks[0]), w0.text, re.I):
            continue
        cy = w0.top + w0.height / 2
        row = sorted((wd for wd in page.words if abs(wd.top + wd.height / 2 - cy) < max(12, w0.height * 0.7)),
                     key=lambda wd: wd.left)
        i = row.index(w0)
        if not all(i + k < len(row) and re.fullmatch(re.escape(t), row[i + k].text, re.I) for k, t in enumerate(toks)):
            continue
        grp = row[i:i + len(toks)]
        top, bottom = min(wd.top for wd in grp), max(wd.top + wd.height for wd in grp)
        if side == "below":
            x0 = min(wd.left for wd in grp)
            return (x0, bottom + 2, x0 + w, bottom + 2 + h)
        lab = grp[-1]
        x0 = lab.left + lab.width + 5
        return (x0, top, x0 + w, bottom + h)
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
        # [^\d]+, not \W+: a ruled line under "Closing date" sometimes OCRs as a literal underscore,
        # which \W does not match, so the date was silently never read.
        m = re.search(r"Closing date[^\d]+(\d{4}/\d{2}/\d{2})", t, re.I)
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

# Off for this round: a real-scan check (see tests/fixtures/baseline/crops/) found that label_box() locates
# a field's printed label by requiring an exact OCR word match on one row, which fails on a filled-in,
# handwritten form far more often than it works (6 of 7 labels tried were not even found; the one that was
# produced a crop that did not frame the answer). verify_identifiers and verify_yesno (below) are otherwise
# complete and unit-tested, and nothing calls them while this is False - re-enabling them needs the labels
# to be found a more robust way first (aligning the scan to its blank template and cutting fields out by
# fixed position, not locating text by OCR on a page that may have handwriting obscuring it).
CROP_VERIFY_ENABLED = False

VISION_SYSTEM = (
    "You read scanned South African government tender forms. Look ONLY at the page image you are given. "
    "Report what is actually written, ticked or stamped on the page. Never guess a value that is not visible, "
    "and never copy the printed form text as if it were an answer. Return ONLY a JSON object."
)


def _ask(llm: LlamaClient, facts: Facts, prompt: str, images: list[str]):
    """Called from worker threads under concurrency (vision_extract/inspect_pages/signature_match each run
    a wave of these on a thread pool), so facts.vision_log is updated under a lock, and the catch is
    deliberately broad (not a specific allowlist): a call that fails in a way nobody anticipated must still
    be isolated and recorded here, not propagate up through the thread pool and abandon every other page's
    result that was already in - the exact "a call that still fails must not stop the run" requirement."""
    with facts._lock:
        facts.vision_log["calls"] += 1
    try:
        return llm.complete_json(VISION_SYSTEM, prompt, images=images)
    except Exception as err:  # noqa: BLE001 - intentionally broad; see docstring
        # an LLMError's own str() already is the server's message, shortened and with the key scrubbed
        msg = str(err)[:300] if isinstance(err, LLMError) else f"{type(err).__name__}: {str(err)[:140]}"
        with facts._lock:
            facts.vision_log["failed"] += 1
            if msg not in facts.vision_log["errors"] and len(facts.vision_log["errors"]) < 4:
                facts.vision_log["errors"].append(msg)
        return None


def vision_precheck(llm: LlamaClient, pages: list[Page], facts: Facts) -> bool:
    """Before committing to a full run (which can be 50+ calls), send one real page image and see whether
    the vision model answers at all. If it doesn't, the vision steps are skipped with the reason recorded
    in facts.vision_log['stopped_early'], instead of making every one of those calls fail the same way."""
    if not pages:
        return True
    data = _ask(llm, facts, 'Reply with JSON only: {"ok": true}', [pages[0].image_path])
    if data is None:
        facts.vision_log["stopped_early"] = (llm.errors[-1] if llm.errors else
                                             "the vision model did not answer the test call")
        return False
    return True


def _answer(data, key="answer"):
    if not isinstance(data, dict):
        return None
    v = data.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _second_look_prompt(entry: F.Entry, page: Page) -> str:
    return (f"This is page {page.number} of a bid pack. Question [{entry.key}]: {entry.desc}. "
            "Answer with JSON {\"answer\": \"...\"}. For a yes/no question answer exactly Yes, No, Both (both "
            "boxes marked) or blank (neither marked). For anything else write exactly what is written, or "
            "blank if the field is on this page and empty, or "
            "'not on this page' if it is not on this page.")


def _apply_second_look(fact: Fact, entry: F.Entry, data) -> None:
    """Agreement with the first reading makes it a verified reading; disagreement (or no answer at all)
    leaves it as an observation."""
    if data is None:
        fact.confidence, fact.note = 0.5, "the second look could not be made"
        return
    again = F.clean(entry.kind, _answer(data))
    if again == fact.value and again is not F.NOT_ON_PAGE and again is not F.UNREAD:
        fact.verified, fact.confidence = True, 0.9
    else:
        fact.confidence = 0.4
        fact.note = "a second, differently worded look read this differently"


def _second_look(llm: LlamaClient, facts: Facts, page: Page, entry: F.Entry, fact: Fact) -> None:
    """A reading that would raise an issue (blank, Yes, both boxes, an impossible date) is asked again as a
    single, differently worded question. Kept for anything that still calls a single second look directly
    (tests, other callers); vision_extract itself runs a whole wave of these concurrently - see
    _apply_second_look / _second_look_prompt."""
    data = _ask(llm, facts, _second_look_prompt(entry, page), [page.image_path])
    _apply_second_look(fact, entry, data)


def vision_extract(llm: LlamaClient, pages: list[Page], facts: Facts, local_content: bool = True,
                   verify: bool = True, progress=None, timing: dict | None = None):
    """Read every handwritten entry in forms.FORMS. One call per PAGE (small local models cope badly with
    several images and long answers), then merge: a value on any page beats a 'blank' on another, because
    each entry lives on one page only. Any reading that would raise an issue gets a second look, all run as
    one concurrent wave after every form's first reading is in (see _apply_second_look).

    The first-reading calls (one per page, independent of each other) and the second-look calls (one per
    deviating field, independent of each other) each run on a thread pool sized by
    TENDERLENS_VISION_CONCURRENCY. Results are always matched back up by page/field - the dict a page's
    answer gets merged into and the order facts are added in do not depend on which call finishes first, so
    this gives the same findings a sequential run would, not just the same final report.

    progress(done, total), if given, is called as each first-reading page call finishes, counted across
    every form (not reset per form) - so it tracks the "vision first readings" stage as a whole."""
    from collections import Counter

    forms_here = []
    for dtype, raw_entries in F.FORMS.items():
        entries = [e for e in raw_entries if local_content or not e.applies]
        pgs = [p for p in pages if p.doc_type == dtype]
        if pgs and entries:
            forms_here.append((dtype, entries, pgs))
    total_pages = sum(len(pgs) for _, _, pgs in forms_here)
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], total_pages)

    t0 = time.time()
    pending: list[tuple[F.Entry, Fact, Page]] = []
    for dtype, entries, pgs in forms_here:
        reads: dict[str, list] = {}
        fields = {e.key: f"[{e.kind}] {e.desc}" for e in entries}

        def make_prompt(p, dtype=dtype, fields=fields):
            return (
                f"This image is page {p.number} of a bid pack and belongs to the form '{dtype}'. For EACH field "
                "listed, write what is handwritten, ticked or stamped on THIS page. Use \"not on this page\" if the "
                "field is not on this page, \"blank\" if it is on this page but nothing is written in it, \"unreadable\" if you "
                "cannot read it. For yes/no questions answer exactly Yes, No, Both (both boxes marked) or blank. "
                "Dates: copy them as written. Return JSON of the form {\"<field>\": \"<answer>\"}.\nFields:\n"
                + json.dumps(fields, indent=1))

        tasks = [(lambda p=p: _ask(llm, facts, make_prompt(p), [p.image_path])) for p in pgs]
        datas = run_concurrent(tasks, vision_concurrency(), tick)
        for p, data in zip(pgs, datas):
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
            if e.where == "last":
                # this entry only legitimately exists on the form's last/signing page; a reading claimed on
                # any other page is the model mistakenly answering a field that was "not on this page" (the
                # cause of e.g. a witness count being attributed to a page with no witness section at all)
                got = [(c, pg) for c, pg in got if pg == pgs[-1].number] or got
            values = [(c, pg) for c, pg in got if c is not F.BLANK]
            if values:
                top = Counter(c for c, _ in values).most_common(1)[0][0]
                page_no = next(pg for c, pg in values if c == top)
                fact = facts.add(e.key, top, page_no, "vision", 0.6)
            else:
                page_no = pgs[-1].number if e.where == "last" else got[0][1]
                fact = facts.add(e.key, F.BLANK, page_no, "vision", 0.6)
            if verify and F.is_deviation(e, fact.value):
                pending.append((e, fact, next(p for p in pgs if p.number == fact.page)))
    if timing is not None:
        timing["vision_first"] = timing.get("vision_first", 0.0) + (time.time() - t0)

    if pending:
        t1 = time.time()
        tasks = [(lambda e=e, p=p: _ask(llm, facts, _second_look_prompt(e, p), [p.image_path]))
                for e, _, p in pending]
        results = run_concurrent(tasks, vision_concurrency())
        for (e, fact, _), data in zip(pending, results):
            _apply_second_look(fact, e, data)
        if timing is not None:
            timing["vision_second"] = timing.get("vision_second", 0.0) + (time.time() - t1)


# ---------------------------------------------------------------------------
# Step 3B (FIX_ALL_PROMPT.md): a handwritten identifier that disagrees with the printed copies elsewhere in
# the pack does not go straight into a finding. A focused, enlarged crop of just that field is read twice,
# differently worded, before the disagreement is trusted. Scoped to the fields whose printed label is
# confirmed word-for-word in the blank form templates (samples/blank/*.txt) - not guessed from any pack.
# --------------------------------------------------------------------------

# field key -> (the pool label audit.identifiers() groups it under, the field's printed label as it reads
# in samples/blank, which OCR-sourced field pool to compare against, a shape check or None)
_CROP_FIELDS = {
    "sbd4.id_no": ("SA ID number", "Identity Number", "id.sa_id", "sa_id"),
    "sbd4.reg_no": ("Company registration number", "Company Registration Number", "id.reg_no", None),
    "sbd4.tax_no": ("Tax reference number", "Tax Reference Number", "id.tax_no", "tax_no"),
    "sbd6_1.reg_no": ("Company registration number", "Company registration number", "id.reg_no", None),
}


def _shape_ok(kind: str, digits: str) -> bool:
    from .audit import luhn_ok  # deferred: audit.py imports Facts from this module at load time
    if kind == "sa_id":
        return len(digits) == 13 and luhn_ok(digits)
    if kind == "tax_no":
        return len(digits) == 10
    return True


def _crop_ask_twice(llm: LlamaClient, facts: Facts, page: Page, box, label: str) -> tuple[str, str, str]:
    """Cut `box` out of the page's own rendered scan (not a resized copy), enlarge it, and ask what it says
    twice, worded differently - the same kind of independent-enough second opinion _second_look gives a
    whole-page reading, but focused tightly enough that a misread has a real chance of being corrected."""
    from PIL import Image
    x0, y0, x1, y1 = box
    im = Image.open(page.image_path).convert("RGB")
    pad = 8
    crop = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    tag = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    crop_path = Path(page.image_path).parent / f"crop-{tag}-{page.number}.png"
    crop.save(crop_path)
    q1 = (f"This is a cropped, enlarged part of a government bid form, showing the '{label}' field. What is "
          "written there? Reply with JSON {\"answer\": \"...\"}: exactly what is written, digits only if it "
          "is a number.")
    q2 = ("Zoomed-in crop of one handwritten entry on a form. Transcribe exactly what is written, character "
          "by character. Reply with JSON {\"answer\": \"...\"}.")
    a1 = _answer(_ask(llm, facts, q1, [str(crop_path)]))
    a2 = _answer(_ask(llm, facts, q2, [str(crop_path)]))
    return str(a1 or "").strip(), str(a2 or "").strip(), str(crop_path)


def verify_identifiers(llm: LlamaClient, pages: list[Page], facts: Facts) -> None:
    """Run after vision_extract. Mutates facts in place: a whole-page identifier reading that a focused crop
    (asked twice) clears against the printed value is corrected; one the crop confirms is kept, now with the
    crop itself as its evidence; one the crop cannot settle either way is removed and logged under 'possible
    misreads set aside' instead, with every reading shown, so nothing is silently dropped."""
    from collections import Counter

    from .audit import mask
    if not llm.configured:
        return
    by_no = {p.number: p for p in pages}
    for key, (pool_label, label_text, ocr_field, shape) in _CROP_FIELDS.items():
        candidates = [f for f in facts.items if f.field == key and f.source == "vision"]
        if not candidates:
            continue
        ocr_vals = [f.value.replace(" ", "") for f in facts.items if f.field == ocr_field and f.source == "ocr"]
        if not ocr_vals:
            continue  # nothing printed to compare against; leave this to the rules' own vote
        consensus = Counter(ocr_vals).most_common(1)[0][0]
        for fact in candidates:
            v = str(fact.value).replace(" ", "")
            if v.upper() == consensus.upper():
                continue  # already agrees with print, nothing to verify
            page = by_no.get(fact.page)
            box = page and label_box(page, label_text, "right")
            if not page or not box:
                continue  # can't locate the field on this page; leave the whole-page reading as is
            a1, a2, crop_path = _crop_ask_twice(llm, facts, page, box, label_text)
            n1 = re.sub(r"[^A-Za-z0-9/]", "", a1).upper()
            n2 = re.sub(r"[^A-Za-z0-9/]", "", a2).upper()
            if n1 and n1 == n2 == consensus.upper():
                fact.value = consensus
                fact.note = "a focused, enlarged re-read of just this field (asked twice) matches the printed value elsewhere"
                facts.misread_variants.append(
                    f"{pool_label}: page {fact.page} first read (whole page) as {mask(v)}; a focused re-read of "
                    f"just this field, asked twice, gives {mask(consensus)}, matching the printed value "
                    "elsewhere. Handwriting misread, no action.")
            elif n1 and n1 == n2 and (shape is None or _shape_ok(shape, re.sub(r"\D", "", n1))):
                fact.value, fact.box, fact.verified = n1, box, True
                fact.note = "confirmed by a focused, enlarged re-read of just this field, asked twice"
            else:
                reason = ("the two close-up readings disagree with each other" if n1 != n2 else
                          "the close-up reading does not have the right shape for this field")
                facts.misread_variants.append(
                    f"{pool_label}: page {fact.page} whole-page reading was {mask(v)}; a focused re-read gives "
                    f"'{a1}' then '{a2}' - {reason}. Set aside rather than reported either way; see the crop "
                    f"({Path(crop_path).name}).")
                facts.items.remove(fact)


# ---------------------------------------------------------------------------
# Step 4 item 3 (FIX_ALL_PROMPT.md), scoped to SBD 4: a whole-page reading of a tiny YES/NO tick or circle
# is asked a second time on the same whole page already (_second_look), but that is the same reader looking
# at the same small mark, not a genuinely independent check. These three questions' numbers sit inline, on
# the same printed line as their own 'YES / NO', confirmed word for word in
# samples/blank/SCM-Bid_documents_SBD_4_doc.txt - so a crop of just that line can be anchored on the number
# reliably. Questions 2.7.2, 2.8 and 2.9 are deliberately excluded: their question text has no leading
# number on the line that carries 'YES / NO' at all (checked directly against that template, not assumed -
# an earlier version of this code wrongly included 2.8 on a stray OCR fragment and produced a crop centred
# on the question's paragraph text instead of its answer area).
# --------------------------------------------------------------------------

_SBD4_YESNO = {
    "sbd4.q2_7_state_employed": "2.7",
    "sbd4.q2_10_aware_of_other_bidder_relationship": "2.10",
    "sbd4.q2_11_interest_in_related_companies": "2.11",
}


def _crop_ask_yesno_twice(llm: LlamaClient, facts: Facts, page: Page, box) -> tuple:
    from PIL import Image
    x0, y0, x1, y1 = box
    im = Image.open(page.image_path).convert("RGB")
    pad = 8
    crop = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    crop_path = Path(page.image_path).parent / f"crop-yesno-{page.number}-{x0}-{y0}.png"
    crop.save(crop_path)
    q1 = ("This is a cropped, enlarged part of a bid form, showing one YES/NO question's answer area. Which "
          "is marked: YES, NO, BOTH (both marked), or NEITHER (nothing marked)? Reply with JSON "
          "{\"answer\": \"Yes\", \"No\", \"Both\" or \"Neither\"}.")
    q2 = ("Zoomed-in crop of a YES/NO choice on a government bid form. Look only at what is circled, ticked "
          "or crossed out here - nothing else. Is it Yes, No, Both, or Neither? Reply with JSON "
          "{\"answer\": \"...\"}.")
    a1 = _answer(_ask(llm, facts, q1, [str(crop_path)]))
    a2 = _answer(_ask(llm, facts, q2, [str(crop_path)]))
    return F.clean(F.YESNO, a1), F.clean(F.YESNO, a2), str(crop_path)


def _yn_label(v) -> str:
    return "blank (neither marked)" if v is F.BLANK else str(v)


def verify_yesno(llm: LlamaClient, pages: list[Page], facts: Facts) -> None:
    """SBD 4's yes/no questions, read a second, independent way when the whole-page reading (and its own
    whole-page second look) came back as a deviation: a focused, enlarged crop of just that question's
    answer area, asked twice, differently worded, allowing Yes, No, Both or Neither. A crop that settles on
    one answer both times overrides the whole-page reading, since a small tick or circle is exactly what a
    whole page cannot reliably resolve. A crop that cannot settle is removed and logged under 'possible
    misreads set aside' instead; it then shows up as 'could not be read' rather than as a false finding."""
    if not llm.configured:
        return
    by_no = {p.number: p for p in pages}
    entries = {e.key: e for e in F.FORMS["sbd4"]}
    for key, number in _SBD4_YESNO.items():
        fact, entry = facts.best(key), entries.get(key)
        if not fact or not entry or not F.is_deviation(entry, fact.value):
            continue
        page = by_no.get(fact.page)
        box = page and label_box(page, number, "right", w=820, h=70)
        if not page or not box:
            continue
        a1, a2, crop_path = _crop_ask_yesno_twice(llm, facts, page, box)
        if a1 in (F.UNREAD, F.NOT_ON_PAGE) or a2 in (F.UNREAD, F.NOT_ON_PAGE):
            continue  # the crop itself could not be read; leave the whole-page reading as is
        if a1 == a2:
            before = fact.value
            fact.value, fact.box, fact.verified, fact.confidence = a1, box, True, 0.9
            fact.note = (f"a focused, enlarged crop of question {number}'s answer area (asked twice) reads "
                        f"{_yn_label(a1)}" + (f", not the whole-page reading of {_yn_label(before)}"
                                               if before != a1 else ""))
        else:
            facts.misread_variants.append(
                f"SBD 4 question {number}: the whole-page reading was {_yn_label(fact.value)}; a focused crop "
                f"of just this question's answer area gives {_yn_label(a1)} then {_yn_label(a2)} - the two "
                "close-up readings disagree, so this is set aside rather than reported either way. See the "
                f"crop ({Path(crop_path).name}).")
            facts.items.remove(fact)


def inspect_pages(llm: LlamaClient, pages: list[Page], facts: Facts, passes: int = 2, progress=None,
                  timing: dict | None = None) -> None:
    """Ask a vision model what no rule can see: crossed-out print, correction fluid, notes in the margin, pages
    that are rotated or cut off, scans too poor to read, and whether the signing line holds a real pen signature.
    A 'yes' is a lead. With passes=2 it is repeated as a differently worded question and only a second 'yes'
    makes it a verified reading. The first wave (one call per page) and the second-look wave (one call per
    lead) each run concurrently - see vision_extract's docstring for why that gives the same findings a
    sequential run would. progress(done, total) tracks the first wave only, across every page. This whole
    function (not just its second-look wave) counts as "vision second looks and checks" in `timing` -
    "vision first readings" is specifically vision_extract's reading of the forms' own fields."""
    t_start = time.time()
    from . import vision as V
    signing = {}
    for dtype, idx in F.SIGNING_PAGE.items():
        pgs = [p for p in pages if p.doc_type == dtype]
        if pgs:
            signing[pgs[idx].number] = dtype

    targets = []  # (page, its own question set)
    for p in pages:
        if p.doc_type not in F.INSPECTION_FORMS:
            continue
        qs = {k: v[0] for k, v in F.INSPECTIONS.items()}
        cutoff = V.page_cutoff(p, pages)
        if cutoff is not None:
            # settled without a model call: a real cut-off page is reported directly, a clearly whole one
            # is simply not asked about (Step 4.5 - "use the vision model only if that check cannot decide")
            qs.pop("cut_off", None)
            if cutoff:
                facts.add("inspect.cut_off", True, p.number, "image", 0.95, verified=True,
                          note="confirmed by a non-AI check of the page size and margins, not a vision reading")
        if p.number in signing:
            qs["signature_seen"] = ("Is there a handwritten pen signature (not a typed name, and not only a printed "
                                    "line) beside or above the word 'Signature'?")
        targets.append((p, qs))

    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], len(targets))

    def make_prompt(p, qs):
        return (f"This image is page {p.number} of a bid pack (form '{p.doc_type}'). Answer each question with "
                "exactly yes or no. Say yes only if you can clearly see it. Return JSON {\"<key>\": \"yes\" or "
                "\"no\"}.\n" + json.dumps(qs, indent=1))

    tasks = [(lambda p=p, qs=qs: _ask(llm, facts, make_prompt(p, qs), [p.image_path])) for p, qs in targets]
    datas = run_concurrent(tasks, vision_concurrency(), tick)

    pending: list[tuple[Fact, Page, str]] = []
    for (p, qs), data in zip(targets, datas):
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
                pending.append((fact, p, second_q))

    if pending:
        tasks2 = [(lambda p=p, second_q=second_q: _ask(
                      llm, facts, f"Page {p.number}. {second_q} Answer JSON {{\"answer\": \"yes\" or \"no\"}}.",
                      [p.image_path]))
                 for _, p, second_q in pending]
        results = run_concurrent(tasks2, vision_concurrency())
        for (fact, _, _), data in zip(pending, results):
            again = F.clean(F.BOOL, _answer(data))
            if again is True:
                fact.verified, fact.confidence = True, 0.9
            else:
                fact.confidence, fact.note = 0.4, "a second, differently worded look did not confirm it"
    if timing is not None:
        timing["vision_second"] = timing.get("vision_second", 0.0) + (time.time() - t_start)


def signature_match(llm: LlamaClient, pages: list[Page], facts: Facts, progress=None,
                    timing: dict | None = None) -> None:
    """Compare each signing page's signature with the quotation's, as one picture with the two stacked (A over B).
    Handwriting similarity is a lead for a person, never proof, so the audit only ever reports it as an observation.
    Every comparison's two orderings (A-over-B and B-over-A) run concurrently, across every form being compared."""
    t_start = time.time()
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
    targets = []  # (dtype, page, A-over-B stacked image, B-over-A stacked image)
    for dtype, (p, im) in crops.items():
        if dtype == ref:
            continue
        base = Path(p.image_path).parent
        ab_path = stacked(crops[ref][1], im, base / f"sig-{ref}-{dtype}.png")
        ba_path = stacked(im, crops[ref][1], base / f"sig-{dtype}-{ref}.png")
        targets.append((dtype, p, ab_path, ba_path))
    if not targets:
        return

    tasks = []
    for _, _, ab_path, ba_path in targets:
        tasks.append(lambda path=ab_path: _ask(llm, facts, ask, [path]))
        tasks.append(lambda path=ba_path: _ask(llm, facts, ask, [path]))
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], len(tasks))

    results = run_concurrent(tasks, vision_concurrency(), tick)
    for i, (dtype, p, _, _) in enumerate(targets):
        a = str(_answer(results[2 * i]) or "").lower()
        b = str(_answer(results[2 * i + 1]) or "").lower()
        if a.startswith("diff") and b.startswith("diff"):
            f = facts.add(f"sigmatch.{dtype}", "different", p.number, "vision", 0.9, verified=True)
        elif a.startswith("diff") or b.startswith("diff"):
            f = facts.add(f"sigmatch.{dtype}", "different", p.number, "vision", 0.4,
                          note="the two orderings of the pair disagreed")
        else:
            continue
        f.note = f.note or f"compared with the {ref} signature"
    if timing is not None:
        timing["vision_second"] = timing.get("vision_second", 0.0) + (time.time() - t_start)
