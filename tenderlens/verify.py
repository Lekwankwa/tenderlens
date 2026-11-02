"""Requirement-driven verification: for each checklist item, find the pack pages that probably show it, have the vision
model read them into structured fields, and let code decide pass/fail (dates against the closing date, numbers against
the rule). The model never returns a verdict. A failure is confirmed only by a second, reworded read that fails the
same way and quotes its evidence; otherwise it stays an observation."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date

from .checklist import Item
from .pricing import READ_TIMEOUT

TOP_PAGES = 3
MAX_CALLS = 200

FIELDS = """Return JSON:
{"present": true|false,        // is the document/requirement described above actually shown on these pages
 "signed": true|false|null,    // is it signed (null when no signature is called for)
 "date_issued": "YYYY-MM-DD"|null, "valid_until": "YYYY-MM-DD"|null,
 "value": "the figure or grade asked about, as printed"|null,
 "quote": "a short verbatim phrase from the page that supports your answer", "page": 0}
Read only what is printed. Use null when you cannot read it. Do not judge compliance."""


@dataclass
class Verdict:
    item: Item
    status: str                 # pass | fail | missing | unread
    reason: str = ""
    pages: list[int] = field(default_factory=list)
    quote: str = ""
    confirmed: bool = False


def _words(s: str) -> set[str]:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[a-z]{4,}", s.lower())}


_GENERIC = {"form", "document", "documents", "copy", "valid", "attach", "attached", "submit", "bidder", "tender",
            "with", "from", "this", "that", "must", "including", "completed", "signed", "certificate", "original"}


def rank_pages(pages, item: Item, top: int = TOP_PAGES) -> list:
    """Pack pages whose OCR text shares the most distinctive words with the item's label; form codes (SBD 4) count extra."""
    want = _words(item.label) - _GENERIC or _words(item.label)
    codes = re.findall(r"\b[SM]BD\s*(\d(?:\.\d)?)", item.label, re.I)
    scored = []
    quote = re.sub(r"[^a-z0-9]+", "", item.source_quote.lower())
    for p in pages:
        if p.doc_type == "blank" or len(p.text.strip()) < 40:
            continue
        low = p.text.lower()
        # a page that holds the tender's own sentence is the buyer's text copied into the pack, not the bidder's answer
        if len(quote) >= 30 and quote in re.sub(r"[^a-z0-9]+", "", low):
            continue
        score = len(want & _words(low)) / max(1, len(want))
        if codes and any(re.search(rf"\b[sm]bd\s*{re.escape(c)}\b", low) for c in codes):
            score += 1.0
        if score >= 0.6:
            scored.append((score + (0.15 if p.doc_type != "unknown" else 0), p))
    scored.sort(key=lambda t: (-t[0], t[1].number))
    return [p for _, p in scored[:top]]


def prompt(item: Item, reworded: bool = False) -> str:
    ask = (f"The tender requires: {item.label}." + (f" Rule: {item.rule}." if item.rule else ""))
    if reworded:
        ask = (f"Look at the page image(s) and decide what is printed about: {item.label}."
               + (f" The tender's test is: {item.rule}." if item.rule else "")
               + " Report the dates, signature and figure exactly as shown.")
    return f"{ask}\n\n{FIELDS}"


def parse_date(s) -> date | None:
    if not isinstance(s, str):
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    try:
        return date(int(m[1]), int(m[2]), int(m[3])) if m else None
    except ValueError:
        return None


def _months_limit(rule: str) -> int | None:
    m = re.search(r"(?:not older than|within|issued within|less than)\s+(\d+)\s*(month|day|year)", rule, re.I)
    if not m:
        return None
    n, unit = int(m[1]), m[2].lower()
    return n * (30 if unit == "month" else 365 if unit == "year" else 1) if unit != "day" else n


def _number(s) -> float | None:
    m = re.search(r"\d[\d  ,]*(?:\.\d+)?", str(s or ""))
    if not m:
        return None
    try:
        return float(re.sub(r"[  ,]", "", m[0]))
    except ValueError:
        return None


def decide(item: Item, ans: dict | None, closing: date | None) -> tuple[str, str]:
    """(status, reason) from one structured answer - all comparisons are done here, in code."""
    if not isinstance(ans, dict):
        return "unread", ""
    if ans.get("present") is False:
        return "missing", f"{item.label}: not shown on the pages that looked closest"
    kind = item.kind
    if kind == "signature" or (kind == "document" and ans.get("signed") is False and "sign" in item.rule.lower()):
        if ans.get("signed") is False:
            return "fail", f"{item.label} is not signed"
    if kind in ("date_validity", "document", "value_rule", "identity") or parse_date(ans.get("valid_until")):
        until, issued = parse_date(ans.get("valid_until")), parse_date(ans.get("date_issued"))
        if closing and until and until < closing:
            return "fail", f"{item.label} expired on {until.isoformat()}, before the closing date {closing.isoformat()}"
        limit = _months_limit(item.rule)
        if closing and issued and limit and (closing - issued).days > limit:
            return "fail", (f"{item.label} was issued on {issued.isoformat()}, more than "
                            f"{item.rule.split('than')[-1].strip() if 'than' in item.rule else str(limit) + ' days'} before the closing date")
    if kind == "value_rule":
        got, need = _number(ans.get("value")), _number(item.rule)
        low = item.rule.lower()
        if got is not None and need is not None:
            if re.search(r"minimum|at least|or higher|or more|>=|not less", low) and got < need:
                return "fail", f"{item.label}: {ans.get('value')} is below the required {item.rule}"
            if re.search(r"maximum|at most|or lower|or less|<=|not more|not exceed", low) and got > need:
                return "fail", f"{item.label}: {ans.get('value')} is above the allowed {item.rule}"
    return "pass", ""


def verify_item(llm, facts, pages, item: Item, closing: date | None, budget: "CallBudget") -> Verdict:
    from .facts import _ask
    cands = rank_pages(pages, item)
    if not cands:
        return Verdict(item, "missing", f"{item.label}: no page of the pack looks like it")
    images = [p.image_path for p in cands]
    nos = [p.number for p in cands]
    if not budget.take():
        return Verdict(item, "unread", "call cap reached", nos)
    first = _ask(llm, facts, prompt(item), images, nos[0], READ_TIMEOUT)
    status, reason = decide(item, first, closing)
    quote = str((first or {}).get("quote") or "")
    pg = [n for n in nos if (first or {}).get("page") == n] or nos[:1]
    v = Verdict(item, status, reason, pg, quote)
    # "not signed" on a page the pack copied from the tender (a blank template) is the commonest false alarm; the
    # signature checks of the audit own that question, so here it is never confirmed into an issue
    if status in ("fail", "missing") and budget.take():
        second = _ask(llm, facts, prompt(item, reworded=True), images, nos[0], READ_TIMEOUT)
        s2, _ = decide(item, second, closing)
        q2 = str((second or {}).get("quote") or "")
        v.confirmed = s2 == status and bool(quote or q2) and not reason.endswith("is not signed")
        v.quote = quote or q2
    return v


class CallBudget:
    def __init__(self, cap: int = MAX_CALLS) -> None:
        self.cap, self.used, self._lock = cap, 0, threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.cap:
                return False
            self.used += 1
            return True


def run_verification(llm, pages, facts, items: list[Item], closing_date: str = "", workers: int | None = None,
                     cap: int = MAX_CALLS) -> list[Verdict]:
    """One rolling-window pass over the checklist; the verdicts are stored on facts.verdicts."""
    from .llm import run_rolling, vision_concurrency
    if not items:
        return []
    closing = parse_date(closing_date)
    budget = CallBudget(cap)
    out = run_rolling([(lambda it=it: verify_item(llm, facts, pages, it, closing, budget)) for it in items],
                      workers or vision_concurrency(), return_exceptions=True)
    verdicts = [v for v in out if isinstance(v, Verdict)]
    facts.verdicts = getattr(facts, "verdicts", []) + verdicts
    return verdicts
