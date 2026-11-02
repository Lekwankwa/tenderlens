"""The full requirement checklist of a long tender.

`requirements.llm_extract` sends one shortened excerpt to one call, which on a 369-page tender returned about six
documents. Here the tender text is cut into sections, each group of sections gets its own extraction call (run on the
thread pool), and every item must carry a verbatim quote that occurs in the tender. The items are what the
verification pass (`verify.py`) later checks the bidder's pages against."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from .llm import ModelClient, run_rolling
from .requirements import quote_in, _norm, _text, _items

MIN_TENDER_CHARS = 40 * 1800  # about 40 pages of text; shorter tenders keep the one-call path
GROUP_CHARS = 24_000
MAX_ITEMS = 120
KINDS = ("document", "date_validity", "signature", "value_rule", "identity")

_HEADING = re.compile(
    r"^\s*(?:(?:part|section|annexure|schedule|appendix|volume)\s+[A-Z0-9][\w.\-]*\b.*|"
    r"(?:returnable|evaluation|tender data|contract data|special conditions|scope of work|"
    r"conditions of tender|eligibility|functionality|mandatory|compulsory)\b.*|"
    r"\d{1,2}(?:\.\d{1,2})*\s+[A-Z][A-Za-z ,&/\-]{3,60})\s*$", re.I)

SYSTEM = (
    "You list what a bidder must submit or satisfy, from part of a South African tender document. "
    "Return ONLY JSON. Never invent an item. Every item needs a verbatim source_quote copied from the text."
)
SCHEMA_HINT = """Return JSON:
{"items": [{"id": "snake_case", "label": "short name of the document or requirement",
 "kind": "document | date_validity | signature | value_rule | identity",
 "rule": "the test in a few words, e.g. 'CIDB grading 4CE or higher', 'valid on the closing date',
          'not older than 3 months', 'minimum indemnity R5 000 000', 'must be signed', or ''",
 "source_quote": "..."}]}
Use kind=document for something to be attached, date_validity for a certificate/letter that must be current,
signature for a form that must be signed, value_rule for a minimum/maximum/grade, identity for a number that must
match across documents. List only items the bidder has to provide or meet. Return {"items": []} if there are none."""


@dataclass
class Item:
    id: str
    label: str
    kind: str
    rule: str
    source_quote: str

    def to_dict(self):
        return asdict(self)


def split_sections(text: str) -> list[str]:
    """Cut at heading-like lines; a section with no headings stays one piece."""
    lines = text.splitlines()
    cuts = [0] + [i for i, ln in enumerate(lines) if i and _HEADING.match(ln) and len(ln) < 90]
    cuts.append(len(lines))
    out = ["\n".join(lines[a:b]).strip() for a, b in zip(cuts, cuts[1:])]
    return [s for s in out if s]


def group_sections(sections: list[str], limit: int = GROUP_CHARS) -> list[str]:
    """Pack consecutive sections into groups of at most `limit` characters (an oversize section is split on lines)."""
    pieces: list[str] = []
    for s in sections:
        while len(s) > limit:
            cut = s.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            pieces.append(s[:cut])
            s = s[cut:].lstrip("\n")
        pieces.append(s)
    groups, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > limit:
            groups.append(cur)
            cur = ""
        cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        groups.append(cur)
    return groups


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def loose_quote_in(source_alnum: str, quote: str) -> bool:
    """The quote occurs in the tender once spacing, punctuation and case are ignored. A quote the model shortened with
    "..." counts when every piece of 12+ characters occurs."""
    parts = [_alnum(x) for x in re.split(r"\.{3}|\u2026", quote)]
    parts = [x for x in parts if len(x) >= 12]
    return bool(parts) and all(x in source_alnum for x in parts)


def parse_items(data: dict, source: str, dropped: list[str] | None = None) -> list[Item]:
    out = []
    src = _alnum(source)
    for d in _items((data or {}).get("items")):
        label, quote = _text(d.get("label")), _text(d.get("source_quote"))
        if not label:
            continue
        if not (quote_in(source, quote) or loose_quote_in(src, quote)):
            if dropped is not None:
                dropped.append(label)
            continue
        kind = _text(d.get("kind")).lower()
        out.append(Item(_text(d.get("id")) or _key(label).replace(" ", "_")[:40], label,
                        kind if kind in KINDS else "document", _text(d.get("rule")), quote))
    return out


def dedupe(items: list[Item], cap: int = MAX_ITEMS) -> list[Item]:
    """Same normalised label (or same quote) is one item; the longer rule wins. Document order is kept."""
    seen: dict[str, int] = {}
    quotes: dict[str, int] = {}
    out: list[Item] = []
    for it in items:
        k, q = _key(it.label), _norm(it.source_quote)
        at = seen.get(k, quotes.get(q))
        if at is not None:
            if len(it.rule) > len(out[at].rule):
                out[at].rule = it.rule
            continue
        seen[k] = quotes[q] = len(out)
        out.append(it)
    return out[:cap]


def build_checklist(llm: ModelClient, tender_text: str, workers: int = 8, cap: int = MAX_ITEMS) -> tuple[list[Item], list[str]]:
    """(items, notes). Returns ([], notes) for a short tender or when the model is not configured; a failed group
    is skipped with a note, never stopping the audit."""
    if llm is None or not llm.configured:
        return [], ["Checklist skipped (no model endpoint)."]
    if len(tender_text) < MIN_TENDER_CHARS:
        return [], []
    groups = group_sections(split_sections(tender_text))
    dropped: list[str] = []

    def ask(g: str):
        return lambda: llm.complete_json(SYSTEM, f"--- TENDER TEXT ---\n{g}\n\n{SCHEMA_HINT}")

    results = run_rolling([ask(g) for g in groups], workers, return_exceptions=True)
    items, notes = [], []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            notes.append(f"Checklist part {i + 1} of {len(groups)} failed ({type(r).__name__}); skipped.")
            continue
        items.extend(parse_items(r, tender_text, dropped))
    if dropped:
        notes.append(f"{len(dropped)} checklist items dropped (quote not in the tender).")
    final = dedupe(items, cap)
    notes.append(f"Checklist: {len(final)} requirements from {len(groups)} parts of the tender.")
    return final, notes
