"""Arithmetic checks on a tender's priced pages (bill of quantities, pricing summary, form of offer).

OCR reads these printed tables badly (13 640.00 becomes 1 640.00, a decimal point disappears), so the numbers are read from
the page image by the vision model, as printed and uncalculated, and ordinary code does every sum. A discrepancy is only an
issue when a second, differently worded reading of the same page produces it again; otherwise it stays an observation.

Checks, each a pure function of the readings (no model, no I/O):
  item      quantity x rate must equal the amount written on the line
  page      brought forward + the page's amounts must equal the total carried forward
  handover  what one page carries forward must be what the next pricing page brings forward
  summary   VAT must be the stated percentage (15% unless the line says otherwise) of the amount before VAT; percentage
            lines (escalation, contingency) must match the percentage they state; the total must be the sum of its parts
  sections  every amount a page carries "to summary" must appear among the summary's section amounts
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ingest import Page

VAT_RATE = 15.0
_STRONG = re.compile(r"BILL OF QUANTIT|CARRIED FORWARD|BROUGHT FORWARD|CARRIED TO|PRICING DATA|SUMMARY OF SECTIONS|"
                     r"SUMMARY OF PRICES|SCHEDULE OF (?:PRICES|RATES|QUANTITIES)|PRICING SCHEDULE|TOTAL BID|"
                     r"FORM OF OFFER|\bVAT\b|VALUE ADDED TAX", re.I)
_WEAK = [r"\bQUANTITY\b|\bQTY\b", r"\bRATE\b", r"\bAMOUNT\b", r"\bTOTAL\b", r"SUB-?TOTAL", r"\bSUMMARY\b", r"\bUNIT\b"]
_MONEY_IN_TEXT = re.compile(r"\d[\d ]*[.,]\d{2}\b")
MAX_PAGES = 60  # most pricing pages read per pack (a cost guard; the highest-scoring pages come first)


def page_score(p: Page) -> float | None:
    """How strongly one page looks like a priced schedule, or None if it does not. Depends on the page's own text only, so a
    page can be judged the moment its OCR is in (see PricingReader)."""
    if p.doc_type == "blank" or len(p.text.strip()) < 60:
        return None
    nums = len(_MONEY_IN_TEXT.findall(p.text))
    strong = bool(_STRONG.search(p.text))
    weak = sum(bool(re.search(w, p.text, re.I)) for w in _WEAK)
    if (strong and nums >= 2) or (weak >= 3 and nums >= 6):
        return nums + 5 * strong + weak
    return None


def pricing_pages(pages: list[Page]) -> list[Page]:
    """Pages that look like priced schedules, favouring recall: a page missed is a defect missed, and a page read
    needlessly costs one cheap call. Strong cue plus a couple of amounts, or the column words plus many amounts."""
    scored = [(sc, p) for p in pages if (sc := page_score(p)) is not None]
    top = sorted(scored, key=lambda t: -t[0])[:MAX_PAGES]
    return sorted((p for _, p in top), key=lambda p: p.number)


def parse_money(value) -> float | None:
    """'R 11 790.00' -> 11790.0, '1,852,656.00' -> 1852656.0, '4 250,50' -> 4250.5; None if it is not an amount."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[Rr]\s*", "", str(value)).replace(" ", " ").strip()
    s = s.replace("−", "-").replace("(", "-").replace(")", "")
    if not re.fullmatch(r"-?\d[\d ,.]*", s):
        return None
    m = re.search(r"[.,](\d{1,2})$", s)
    whole, frac = (s[:m.start()], m.group(1)) if m else (s, "")
    whole = re.sub(r"[ ,.]", "", whole)
    if not re.fullmatch(r"-?\d+", whole):
        return None
    return float(f"{whole}.{frac}" if frac else whole)


@dataclass
class Discrepancy:
    kind: str          # item | page | handover | vat | percent | total | sections
    page: int
    ref: str           # the row or line it concerns, for matching a second reading
    title: str
    detail: str
    figures: tuple = ()
    confirmed: bool = False


def _close(a: float, b: float) -> bool:
    """Equal to within a rand. Amounts in a schedule are exact to the cent, so only rounding is forgiven: a relative
    tolerance would hide exactly the errors that matter on large totals (R 45,000 on R 30 million is 0.15%)."""
    return abs(a - b) <= max(1.0, 1e-7 * max(abs(a), abs(b)))


def _lines(reading: dict, label: str) -> list[dict]:
    return [ln for ln in (reading.get("lines") or []) if isinstance(ln, dict) and ln.get("label") == label]


def _amount(ln: dict) -> float | None:
    return parse_money(ln.get("amount"))


def check_page(page: int, reading: dict) -> list[Discrepancy]:
    """Every discrepancy that one page's reading shows by itself."""
    out: list[Discrepancy] = []
    rows = [r for r in (reading.get("rows") or []) if isinstance(r, dict)]
    amounts = []
    for r in rows:
        a = parse_money(r.get("amount"))
        if a is None:
            continue
        amounts.append(a)
        q, rate = parse_money(r.get("quantity")), parse_money(r.get("rate"))
        if q is not None and rate is not None and not _close(q * rate, a):
            ref = str(r.get("item") or r.get("description") or "")[:40]
            out.append(Discrepancy("item", page, ref, f"Item {ref}: amount is not quantity x rate",
                                   f"{r.get('quantity')} x {r.get('rate')} = {q * rate:,.2f}, but the amount written is {a:,.2f}.",
                                   (q * rate, a)))
    declared = reading.get("row_count")
    incomplete = isinstance(declared, (int, float)) and len([r for r in rows if parse_money(r.get("amount")) is not None]) < declared
    bf = [_amount(ln) for ln in _lines(reading, "brought_forward") if _amount(ln) is not None]
    cf = [_amount(ln) for ln in (_lines(reading, "carried_forward") + _lines(reading, "carried_to_summary")
                                 + _lines(reading, "page_total")) if _amount(ln) is not None]
    if incomplete:
        pass  # the reader listed fewer rows than the page prints, so a page sum would blame the reader, not the bidder
    elif cf and (amounts or bf):
        expected = (bf[0] if bf else 0.0) + sum(amounts)
        if not _close(expected, cf[-1]) and not (amounts == [] and bf and _close(bf[0], cf[-1])):
            out.append(Discrepancy("page", page, "carried", "Page total does not equal the items on the page",
                                   f"Brought forward {(bf[0] if bf else 0.0):,.2f} plus the page's items {sum(amounts):,.2f} is "
                                   f"{expected:,.2f}, but the page carries forward {cf[-1]:,.2f}.", (expected, cf[-1])))
    elif cf and bf and not amounts and not _close(bf[0], cf[-1]):
        out.append(Discrepancy("page", page, "carried", "Carried forward differs from brought forward on a page with no items",
                               f"Brought forward {bf[0]:,.2f}, carried forward {cf[-1]:,.2f}.", (bf[0], cf[-1])))
    out += _check_summary(page, reading)
    return out


def _check_summary(page: int, reading: dict) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    for ln in _lines(reading, "vat"):
        vat, base = _amount(ln), parse_money(ln.get("base_amount"))
        if vat is None or base is None:
            continue
        pct = ln.get("percent")
        pct = float(pct) if isinstance(pct, (int, float)) else VAT_RATE
        expected = base * pct / 100
        if not _close(vat, expected) or (pct != VAT_RATE and _close(vat, base * pct / 100)):
            shown = f"{pct:g}%"
            out.append(Discrepancy("vat", page, "vat", f"VAT is not {VAT_RATE:g}% of the amount before VAT",
                                   f"{VAT_RATE:g}% of {base:,.2f} is {base * VAT_RATE / 100:,.2f}; the line is {vat:,.2f}"
                                   f" (stated as {shown}).", (base * VAT_RATE / 100, vat)))
    for label in ("escalation", "contingency"):
        for ln in _lines(reading, label):
            amt, base, pct = _amount(ln), parse_money(ln.get("base_amount")), ln.get("percent")
            if amt is None or base is None or not isinstance(pct, (int, float)):
                continue
            if not _close(amt, base * pct / 100):
                out.append(Discrepancy("percent", page, label, f"{label.capitalize()} is not the stated percentage of its base",
                                       f"{pct:g}% of {base:,.2f} is {base * pct / 100:,.2f}; the line is {amt:,.2f}.",
                                       (base * pct / 100, amt)))
    parts = [a for ln in _lines(reading, "section_total") if (a := _amount(ln)) is not None]
    sub = [a for ln in _lines(reading, "subtotal") if (a := _amount(ln)) is not None]
    if parts and sub and not _close(sum(parts), sub[0]):
        out.append(Discrepancy("total", page, "subtotal", "Subtotal does not equal the sum of its sections",
                               f"The sections add up to {sum(parts):,.2f}; the subtotal is {sub[0]:,.2f}.", (sum(parts), sub[0])))
    return out


def check_pack(readings: dict[int, dict]) -> list[Discrepancy]:
    """Discrepancies between pages, on top of check_page for each: hand-over from page to page, and the amounts carried
    "to summary" against the summary's own section lines."""
    out: list[Discrepancy] = []
    pages = sorted(readings)
    for a, b in zip(pages, pages[1:]):
        if b - a > 2:
            continue
        cf = [x for ln in _lines(readings[a], "carried_forward") if (x := _amount(ln)) is not None]
        if not cf:
            continue
        bf = [x for ln in _lines(readings[b], "brought_forward") if (x := _amount(ln)) is not None]
        if cf and bf and not _close(cf[-1], bf[0]):
            out.append(Discrepancy("handover", a, f"to{b}", "Carried forward differs from what the next page brings forward",
                                   f"Page {a} carries forward {cf[-1]:,.2f}; page {b} brings forward {bf[0]:,.2f}.", (cf[-1], bf[0])))
    summary = [p for p in pages if _lines(readings[p], "section_total") or _lines(readings[p], "subtotal")]
    if summary:
        in_summary = [x for p in summary for ln in _lines(readings[p], "section_total") if (x := _amount(ln)) is not None]
        for p in pages:
            for ln in _lines(readings[p], "carried_to_summary"):
                x = _amount(ln)
                if x is not None and in_summary and not any(_close(x, y) for y in in_summary):
                    out.append(Discrepancy("sections", p, f"summary{x:.0f}", "Amount carried to the summary is not in the summary",
                                           f"Page {p} carries {x:,.2f} to the summary; the summary's section amounts are "
                                           f"{', '.join(f'{y:,.2f}' for y in in_summary)}.", (x,)))
    return out


def confirm(first: list[Discrepancy], second: list[Discrepancy]) -> list[Discrepancy]:
    """A discrepancy is confirmed when the second reading of the same page produces one of the same kind about the same
    row or line with the same figures (the amounts compared, to the rand); it stays an observation otherwise, because
    two readings that differ in a figure mean the figure was misread, not mistyped."""
    for d in first:
        d.confirmed = any(e.kind == d.kind and e.page == d.page and e.ref == d.ref and len(e.figures) == len(d.figures)
                          and all(_close(x, y) for x, y in zip(e.figures, d.figures)) for e in second)
    return first


READ_TIMEOUT = 150.0  # seconds per attempt: a table page is a long answer (every row), unlike a yes/no page read

_SHAPE = ('{"row_count": number of priced item lines printed on the page, "rows": [{"item": "...", "quantity": "...", '
          '"rate": "...", "amount": "..."}], "lines": [{"label": "brought_forward | carried_forward | carried_to_summary | page_total | '
          'section_total | subtotal | escalation | contingency | vat | total | other", "text": "the printed label", '
          '"percent": number or null, "base_amount": "the amount this line is a percentage of (for VAT: the amount before '
          'VAT), as printed, or null", "amount": "..."}]}')


def first_prompt(page: Page) -> str:
    return (f"This is page {page.number} of a tender document: a priced schedule (bill of quantities, pricing summary or "
            "form of offer). Copy the figures EXACTLY AS PRINTED, character for character, including any that look wrong. "
            "Never calculate, correct, round or fill anything in. 'rows' are the priced items (one per printed item that has "
            "an amount; copy quantity, rate and amount as printed, and leave a value empty when it is not printed). 'lines' "
            "are the totals, brought-forward and carried-forward amounts, section totals, subtotals, VAT and percentage "
            f"lines. Return JSON of this shape: {_SHAPE}. If the page has no prices return {{\"rows\": [], \"lines\": []}}.")


def second_prompt(page: Page) -> str:
    return (f"Look at page {page.number} again. Read its priced table line by line from the bottom up, writing every number "
            "exactly as it is printed (do not add up, correct or round anything). List the totals, carry-overs, VAT and "
            f"percentage lines, then the priced items. Answer in JSON of this shape: {_SHAPE}.")


def _reread_pages(found: list[Discrepancy]) -> set[int]:
    pages = {d.page for d in found}
    pages |= {int(d.ref[2:]) for d in found if d.kind == "handover" and d.ref[2:].isdigit()}
    return pages


class PricingReader:
    """Reads priced pages while the pack is still being rendered and OCR'd. offer(page) is called from the OCR workers as each
    page's text arrives; a page that looks like a priced schedule is submitted for its first reading at once, so the long table
    reads (the longest model lane) overlap the local OCR phase instead of following it. collect(pages) then returns the first
    readings for exactly the pages pricing_pages() would pick, reading any it has not already read. At most MAX_PAGES reads
    are started ahead of time (a cost guard), so a pack with more candidates than that is finished off in collect()."""

    def __init__(self, llm, facts, gate=None, gate_wait: float = 60.0) -> None:
        """gate: a threading.Event that is set when the buyer-requirements (text) call has finished. Until then pages are
        remembered, not read: up to 25 long table reads fired at once next to that one call left it waiting at the provider for
        minutes (928 s in one pack, against 15 s normally). collect() waits for the gate at most `gate_wait` seconds."""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from .llm import vision_concurrency
        self.llm, self.facts = llm, facts
        self._gate, self._gate_wait = gate, gate_wait
        self._held: list[Page] = []
        self._pool = ThreadPoolExecutor(max_workers=vision_concurrency())
        self._futures: dict[int, object] = {}
        self._lock = threading.Lock()

    def _submit(self, page: Page) -> None:
        from .facts import _ask
        self._futures[page.number] = self._pool.submit(
            _ask, self.llm, self.facts, first_prompt(page), [page.image_path], page.number, READ_TIMEOUT)

    def _open(self) -> bool:
        return self._gate is None or self._gate.is_set()

    def offer(self, page: Page) -> None:
        if page_score(page) is None:
            return
        with self._lock:
            if not self._open():
                self._held.append(page)
            elif page.number not in self._futures and len(self._futures) < MAX_PAGES:
                self._submit(page)

    def release(self) -> None:
        """Start the reads that were held back (call when the gate has opened)."""
        with self._lock:
            held, self._held = self._held, []
            for page in held:
                if page.number not in self._futures and len(self._futures) < MAX_PAGES:
                    self._submit(page)

    def collect(self, pages: list[Page]) -> dict[int, dict | None]:
        """First readings, by page number, for the pages the batch check would read."""
        if self._gate is not None:
            self._gate.wait(self._gate_wait)
        wanted = pricing_pages(pages)
        with self._lock:
            held, self._held = self._held, []        # whatever is still held is read now, gate or no gate
            for p in held:
                if p.number not in self._futures and len(self._futures) < MAX_PAGES:
                    self._submit(p)
            for p in wanted:
                if p.number not in self._futures:
                    self._submit(p)
            futures = {p.number: self._futures[p.number] for p in wanted}
        out = {n: f.result() for n, f in futures.items()}
        self._pool.shutdown(wait=False)
        return out


def run_pricing_check(llm, pages: list[Page], facts, passes: int = 2, timing: dict | None = None,
                      prefetched: dict | None = None, reader_facts=None) -> None:
    """Read every pricing page (in slices for a long pack), check the arithmetic, and read again every page that shows a
    discrepancy. Results go to facts.pricing (a list of Discrepancy) and facts.pricing_pages (the pages read)."""
    import time
    from .facts import _ask
    from .llm import run_in_slices, vision_concurrency
    t0 = time.time()
    cands = pricing_pages(pages)
    facts.pricing_pages = [p.number for p in cands]
    if reader_facts is not None:
        facts.merge(reader_facts)          # the call counts and failures of the reads a PricingReader already made
    if not cands:
        return
    results = [prefetched.get(p.number) for p in cands] if prefetched is not None else run_in_slices([(lambda p=p: _ask(llm, facts, first_prompt(p), [p.image_path], page_no=p.number,
                                         timeout=READ_TIMEOUT)) for p in cands],
                            vision_concurrency())
    readings = {p.number: d for p, d in zip(cands, results) if isinstance(d, dict)}
    found = [d for n in sorted(readings) for d in check_page(n, readings[n])] + check_pack(readings)
    if found and passes >= 2:
        by_number = {p.number: p for p in cands}
        again_pages = sorted(n for n in _reread_pages(found) if n in by_number)
        again = run_in_slices([(lambda n=n: _ask(llm, facts, second_prompt(by_number[n]), [by_number[n].image_path],
                                                 page_no=n, timeout=READ_TIMEOUT)) for n in again_pages], vision_concurrency())
        merged = dict(readings)
        merged.update({n: d for n, d in zip(again_pages, again) if isinstance(d, dict)})
        reread = {n for n, d in zip(again_pages, again) if isinstance(d, dict)}
        second = [d for n in sorted(reread) for d in check_page(n, merged[n])] + check_pack(merged)
        confirm(found, second)
    facts.pricing.extend(found)
    if timing is not None:
        timing["vision_pricing"] = timing.get("vision_pricing", 0.0) + (time.time() - t0)
