"""Step 1 - establish what the buyer is asking for.

The AI model reads the RFQ and the specification and returns a structured checklist.
Because a model can invent requirements, every item must carry a ``source_quote``
that really appears in the source text; items that fail that check are dropped and
reported. A deterministic rules pass runs alongside as a cross-check and as the
offline fallback, so the pipeline still works with no endpoint configured.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .llm import ModelClient, LLMUnavailable

SYSTEM = (
    "You extract tender submission requirements from South African government RFQ documents. "
    "Return ONLY JSON. Never invent a requirement. Every item needs a verbatim source_quote copied "
    "from the text provided."
)

SCHEMA_HINT = """Return JSON:
{
 "closing": {"date": "YYYY-MM-DD", "time": "HH:MM", "source_quote": "..."},
 "submit_to_email": {"value": "...", "source_quote": "..."},
 "min_quote_validity_days": {"value": 30, "source_quote": "..."},
 "required_documents": [{"id": "snake_case", "label": "...", "source_quote": "..."}],
 "conditions": [{"label": "...", "source_quote": "..."}],
 "specification": {"description": "...", "quantity": 0, "delivery_address": "...", "source_quote": "..."}
}
Document ids to use when they apply: tcc, bbbee_certificate, sbd4, sbd8, sbd9, sbd6_1, sbd6_2, annexure_c, csd_report."""


@dataclass
class Requirement:
    id: str
    label: str
    source_doc: str
    source_quote: str
    conditional: str = ""  # e.g. "only where there is local content"


@dataclass
class Requirements:
    closing_date: str = ""
    closing_time: str = ""
    submit_to_email: str = ""
    reference: str = ""
    local_content_required: bool = False
    local_content_basis: str = ""
    local_content_negated: bool = False  # the RFQ/Tender itself says, in its own words, the condition is NOT met
    local_content_negated_quote: str = ""
    min_quote_validity_days: int = 0
    documents: list[Requirement] = field(default_factory=list)
    conditions: list[Requirement] = field(default_factory=list)
    spec: dict = field(default_factory=dict)
    checklist: list[dict] = field(default_factory=list)  # checklist.Item.to_dict() of a long tender's full requirement list
    notes: list[str] = field(default_factory=list)  # provenance / dropped items

    def to_dict(self):
        return asdict(self)


def pdf_text(pdf: Path, work: Path | None = None) -> str:
    """The document's text. A scan has no text layer, so (when `work` is given) it is read by OCR instead of
    coming back empty - which left the requirements to be 'extracted' from nothing."""
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    if work is not None and len(out.strip()) < 40:
        from .ingest import ocr_text
        try:
            return ocr_text(pdf, work)
        except Exception:  # noqa: BLE001 - OCR trouble must not stop the audit; fall back to what text there was
            return out
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in(source: str, quote: str) -> bool:
    return bool(quote) and _norm(quote) in _norm(source)


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}


def condition_negated(topic: str, text: str) -> re.Match | None:
    """Does `text` state, in its own words, that `topic` (e.g. "local content") does NOT apply? Generic so any
    conditional document's own "where there is X" clause can be checked the same way, not just local content.
    Deliberately narrow (explicit negation only) - absence of a positive mention is NOT evidence of this; see
    rules_extract's `buyer_says` for the separate, positive-evidence check."""
    t = re.escape(topic)
    return re.search(rf"\bno\s+{t}\b|\b{t}\b[^.]{{0,60}}\b(?:does not apply|is not applicable|not applicable|"
                     r"not required|does not require)", text, re.I)


def rules_extract(email_text: str, email_name: str, spec_text: str, spec_name: str) -> Requirements:
    r = Requirements()
    t = re.sub(r"[ \t]+", " ", email_text)
    flat = re.sub(r"\s+", " ", t)

    m = re.search(r"closing date and time.*?on or before the\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+AT\s+(\d{1,2})h(\d{2})",
                  flat, re.I)
    if m:
        d, mon, y, hh, mm = m.groups()
        r.closing_date = f"{int(y):04d}-{MONTHS[mon.lower()]:02d}-{int(d):02d}"
        r.closing_time = f"{int(hh):02d}:{mm}"
        r.notes.append("closing date/time read by rules from the RFQ")

    m = re.search(r"FILE NO:\s*([\d/()]+\d{2}/\d{2})", flat, re.I)
    if m:
        r.reference = m.group(1)

    m = re.search(r"submitted only to this central email address:\s*([\w.\-]+@[\w.\-]+)", flat, re.I)
    if m:
        r.submit_to_email = m.group(1)

    m = re.search(r"valid for (?:duration of )?(\d+) days or more", flat, re.I)
    if m:
        r.min_quote_validity_days = int(m.group(1))

    def add(id_, label, pattern, conditional=""):
        mm = re.search(pattern, flat, re.I)
        if mm:
            r.documents.append(Requirement(id_, label, email_name, mm.group(0), conditional))

    add("tcc", "Tax compliance (TCC / SARS TCS PIN)", r"TCC and BEE Certificates")
    add("bbbee_certificate", "B-BBEE certificate or sworn affidavit", r"TCC and BEE Certificates")
    for sbd, label in [("4", "SBD 4 Declaration of Interest"), ("8", "SBD 8 Past SCM Practices"),
                       ("9", "SBD 9 Independent Bid Determination"), ("6.1", "SBD 6.1 Preference Points Claim"),
                       ("6.2", "SBD 6.2 Local Production and Content")]:
        pat = r"SBD forms \(SBD [^)]*\)"
        mm = re.search(pat, flat, re.I)
        if mm and re.search(rf"(?<![\d.]){re.escape(sbd)}(?![\d])", mm.group(0).replace("SBD forms", "")):
            cond = "only where there is local content" if sbd == "6.2" else ""
            r.documents.append(Requirement("sbd" + sbd.replace(".", "_"), label, email_name, mm.group(0), cond))
    mm = re.search(r"SBD forms \(SBD [^)]*Annexure C\)[^.]*\.", flat, re.I)
    if mm:  # verbatim from the source, never hand-typed
        r.documents.append(Requirement("annexure_c", "Annexure C Local Content Declaration: Summary Schedule",
                                       email_name, mm.group(0), "only where there is local content"))
    add("csd_report", "Latest CSD summary report or CSD number",
        r"Latest CSD summary report or CSD number,? Supplier which are not registered with Central Supplier Database\s*\(CSD\)\s*will be disqualified\.?")

    for label, pat in [
        ("Quotes must be firm and valid for at least 30 days", r"quotation must be firm and valid for duration of 30 days or more"),
        ("Submit only to the central email address; other addresses are disqualified",
         r"Quotations which are not received from this email address will be disqualified"),
        ("Unregistered CSD suppliers are disqualified", r"Supplier which are not registered with Central Supplier Database\s*\(CSD\)\s*will be disqualified"),
        ("Quotation subject to General Conditions of Contract (GCC)", r"subjected to General Condition of Contract \(GCC\)"),
        ("Bidder must verify which company was invited; uninvited suppliers may be disqualified",
         r"verify which company was invited"),
    ]:
        mm = re.search(pat, flat, re.I)
        if mm:
            r.conditions.append(Requirement("cond", label, email_name, mm.group(0)))

    # Does local content apply? Only if the BUYER says so. The RFQ asks for SBD 6.2 and the annexures only
    # "where there is local content"; a threshold written on a form by the bidder does not make it apply.
    stripped = re.sub(r"where there is local content[^.]*\.", "", flat, flags=re.I)
    buyer_says = re.search(r"designated sector|stipulated minimum threshold|minimum threshold|local content (?:of|threshold)|\d+\s?%\s*local",
                           stripped + " " + re.sub(r"\s+", " ", spec_text), re.I)
    r.local_content_required = bool(buyer_says)
    r.local_content_basis = (
        f"the buyer's documents mention: '{buyer_says.group(0)}'" if buyer_says else
        "the RFQ asks for SBD 6.2 and Annexure C only 'where there is local content' and neither the RFQ nor "
        "the specification names a designated sector or a local content threshold")
    negated = condition_negated("local content", stripped + " " + re.sub(r"\s+", " ", spec_text))
    r.local_content_negated = bool(negated)
    r.local_content_negated_quote = negated.group(0) if negated else ""

    # spec sheet (OCR of a scan, so keep patterns loose). Optional: no separate spec document was
    # necessarily issued, so an empty spec_text leaves r.spec = {} rather than a dict of Nones.
    spec = {}
    if spec_text:
        s = re.sub(r"\s+", " ", spec_text)
        spec["source_doc"] = spec_name
        mm = re.search(r"(\d{2,3}\s?AH[^\n]{0,40}?battery\s*\(\d+\))", s, re.I)
        if mm:
            spec["description"] = mm.group(1).strip()
        mm = re.search(r"\(\d+\)\s*(\d{1,4})\b", s)
        if mm:
            spec["quantity"] = int(mm.group(1))
        mm = re.search(r"Delivery ad+ress\s*(.{10,80}?Pretoria)", s, re.I)
        if mm:
            spec["delivery_address"] = mm.group(1).strip()
        mm = re.search(r"Required for .{5,80}?(?=\s*(?:Delivery|$))", s, re.I)
        if mm:
            spec["motivation"] = mm.group(0).strip()
    r.spec = spec
    return r


# Words that mark the passages a buyer's requirements live in; used to pick what to send when a tender is too long.
_KEY_WORDS = re.compile(
    r"closing|clos(?:e|es) at|deadline|submit|e-?mail|validity|valid for|compulsory|mandatory|briefing|clarification|"
    r"CIDB|grading|B-?BBEE|tax (?:compliance|clearance)|CSD|SARS|required|must be|shall be|returnable|"
    r"evaluation|functionality|preference|disqualif|insurance|guarantee|specification|quantity|deliver", re.I)


def prompt_excerpt(text: str, limit: int) -> tuple[str, bool]:
    """(text to put in the prompt, whether it was cut). A tender of hundreds of pages cannot go to the model whole:
    over `limit` characters the opening is kept, then every line that mentions a requirement word (with the line
    after it) in document order, until the limit. The quotes the model returns are still checked against the FULL
    text (see llm_extract), so a shortened prompt cannot let an invented requirement through."""
    if len(text) <= limit:
        return text, False
    lines = text.splitlines()
    keep = [False] * len(lines)
    used = 0
    for i, ln in enumerate(lines):
        if used >= limit * 0.2:
            break
        keep[i] = True
        used += len(ln) + 1
    for i, ln in enumerate(lines):
        if used >= limit:
            break
        if not keep[i] and _KEY_WORDS.search(ln):
            for j in (i, i + 1):
                if j < len(lines) and not keep[j]:
                    keep[j] = True
                    used += len(lines[j]) + 1
    out, gap = [], False
    for ln, k in zip(lines, keep):
        if k:
            if gap:
                out.append("[... text left out ...]")
            out.append(ln)
        gap = not k
    return "\n".join(out), True


def tender_prompt_limit() -> int:
    """TENDERLENS_TENDER_PROMPT_CHARS: characters of tender text sent to the model (default 120,000, about 30,000
    tokens); a longer tender is shortened by prompt_excerpt()."""
    raw = os.environ.get("TENDERLENS_TENDER_PROMPT_CHARS", "").strip()
    return max(5000, int(raw)) if raw.isdigit() else 120_000


def _text(v) -> str:
    return v if isinstance(v, str) else ""


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _items(v) -> list[dict]:
    return [d for d in v if isinstance(d, dict)] if isinstance(v, list) else []


def llm_extract(llm: ModelClient, email_text: str, email_name: str, spec_text: str, spec_name: str) -> Requirements:
    shown_email, cut_email = prompt_excerpt(email_text, tender_prompt_limit())
    shown_spec, cut_spec = prompt_excerpt(spec_text, tender_prompt_limit() // 2)
    user = (
        f"--- DOCUMENT: {email_name} ---\n{shown_email}\n\n--- DOCUMENT: {spec_name} (OCR) ---\n{shown_spec}\n\n{SCHEMA_HINT}"
    )
    data = llm.complete_json(SYSTEM, user)
    source = email_text + "\n" + spec_text
    r = Requirements()
    # The model can answer any field with null, a number or a nested object (a long CE tender made it do so). Every
    # field below is read through _text/_obj so an odd answer drops that one field instead of stopping the audit.
    c = _obj(data.get("closing"))
    if quote_in(source, _text(c.get("source_quote"))):
        r.closing_date, r.closing_time = _text(c.get("date")), _text(c.get("time"))
    else:
        r.notes.append("LLM closing date dropped: source_quote not found in documents")
    e = _obj(data.get("submit_to_email"))
    email = _text(e.get("value"))
    if email and quote_in(source, _text(e.get("source_quote"))) and email in source:
        r.submit_to_email = email
    v = _obj(data.get("min_quote_validity_days"))
    if quote_in(source, _text(v.get("source_quote"))):
        try:
            r.min_quote_validity_days = int(v.get("value") or 0)
        except (TypeError, ValueError):
            r.notes.append(f"LLM validity dropped (not a number): {v.get('value')!r}")
    for d in _items(data.get("required_documents")):
        if quote_in(source, _text(d.get("source_quote"))):
            r.documents.append(Requirement(_text(d.get("id")), _text(d.get("label")), email_name, _text(d["source_quote"])))
        else:
            r.notes.append(f"LLM requirement dropped (quote not in source): {d.get('label')}")
    for d in _items(data.get("conditions")):
        if quote_in(source, _text(d.get("source_quote"))):
            r.conditions.append(Requirement("cond", _text(d.get("label")), email_name, _text(d["source_quote"])))
        else:
            r.notes.append(f"LLM condition dropped (quote not in source): {d.get('label')}")
    sp = _obj(data.get("specification"))
    if sp:
        r.spec = {**sp, "source_doc": spec_name}
    if cut_email or cut_spec:
        r.notes.append(f"The tender text was longer than the model prompt limit ({tender_prompt_limit():,} characters), "
                       "so only its opening and the passages that mention requirements were sent to the model.")
    return r


def merge(llm_r: Requirements | None, rules_r: Requirements) -> Requirements:
    """Rules result is the floor. Anything the AI model found that rules missed is added and tagged;
    any disagreement on scalar values is recorded for a human instead of silently picking one."""
    if llm_r is None:
        rules_r.notes.append("Requirements built by rules only (no model endpoint configured).")
        return rules_r
    out = rules_r
    for fld in ("closing_date", "closing_time", "submit_to_email", "min_quote_validity_days"):
        a, b = getattr(llm_r, fld), getattr(rules_r, fld)
        if a and b and a != b:
            out.notes.append(f"The AI model and rules disagree on {fld}: model={a!r} rules={b!r} -> needs review")
        elif a and not b:
            setattr(out, fld, a)
            out.notes.append(f"{fld} supplied by the AI model only")
    have = {d.id for d in out.documents}
    for d in llm_r.documents:
        if d.id not in have:
            out.documents.append(d)
            out.notes.append(f"Document requirement '{d.id}' supplied by the AI model only")
    out.notes.extend(llm_r.notes)
    out.notes.append("Requirements cross-checked: AI model + rules.")
    return out


def build_requirements(rfq_pdfs: Path | list[Path], spec_text: str, spec_name: str, llm: ModelClient | None,
                       local_content: str = "auto", work: Path | None = None) -> Requirements:
    """rfq_pdfs: the buyer's RFQ, as one path or several (a cover email/letter plus any attachments it came
    with - annexures, a BOQ, blank SBD templates sent separately). Their text is concatenated in the order
    given, so a requirement stated in any one of them is found regardless of how the buyer split them up.
    local_content: 'auto' (decide from the buyer's documents), 'yes' or 'no' (the bidder knows better)."""
    rfqs = [rfq_pdfs] if isinstance(rfq_pdfs, (str, Path)) else list(rfq_pdfs)
    email_text = "\n\n".join(pdf_text(p, work) for p in rfqs)
    email_name = rfqs[0].name if len(rfqs) == 1 else f"{rfqs[0].name} (+{len(rfqs) - 1} more)"
    rules_r = rules_extract(email_text, email_name, spec_text, spec_name)
    if local_content in ("yes", "no"):
        rules_r.local_content_required = local_content == "yes"
        rules_r.local_content_basis = f"set by the user ({local_content})"
    llm_r = None
    if llm is not None and llm.configured:
        try:
            llm_r = llm_extract(llm, email_text, email_name, spec_text, spec_name)
        except Exception as ex:  # noqa: BLE001 - whatever the model returned, the audit goes on with the rules alone
            rules_r.notes.append(f"AI model call failed ({type(ex).__name__}: {ex}); used rules only.")
    out = merge(llm_r, rules_r)
    if llm is not None and llm.configured and os.environ.get("TENDERLENS_CHECKLIST", "1") != "0":
        from .checklist import build_checklist
        try:
            items, notes = build_checklist(llm, email_text + "\n" + spec_text)
            out.checklist = [i.to_dict() for i in items]
            out.notes.extend(notes)
        except Exception as ex:  # noqa: BLE001 - the audit goes on without the checklist
            out.notes.append(f"Checklist failed ({type(ex).__name__}); skipped.")
    return out
