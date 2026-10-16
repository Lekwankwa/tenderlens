"""Step 3 - audit the response against the requirements. Deterministic code, no model.

Every finding says what was found, cites the pages and the rule it relates to, how the
fact was obtained (ocr / manual / vision / image), and what a human should look at.
The wording is "needs review", never "disqualified": the buyer decides that.
"""
from __future__ import annotations

import glob
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import forms as F
from . import vision as V
from .facts import ENTRY, Facts
from .ingest import Page, load_templates
from .requirements import Requirements

DOC_LABEL = {
    "quote": "Quotation", "spec": "Specification sheet", "sbd6_1": "SBD 6.1", "sbd6_2": "SBD 6.2",
    "local_content_decl": "Local content declaration", "sbd4": "SBD 4", "sbd8": "SBD 8", "sbd9": "SBD 9",
    "tcs": "SARS tax compliance status", "bbbee_affidavit": "B-BBEE sworn affidavit",
    "csd": "CSD summary report", "cipc": "CIPC registration certificate",
}
# pages in the standard forms as issued (from the blank templates / page footers)
TEMPLATE_PAGES = {"sbd6_1": 5, "sbd6_2": 3, "local_content_decl": 2, "sbd4": 4, "sbd8": 2, "sbd9": 4}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
# Pairs of digits/letters that OCR commonly swaps on a 150 dpi scan.
_CONFUSABLE = {frozenset(x) for x in ("14", "17", "49", "38", "56", "58", "68", "08", "06", "27", "89", "09", "5S", "0O",
                                      "1I", "1L", "2Z", "8B")}


def only_confusable_diff(a: str, b: str) -> bool:
    """True when a and b differ only where a scanner could plausibly swap the characters."""
    if len(a) != len(b):
        return False
    return all(x == y or frozenset((x.upper(), y.upper())) in _CONFUSABLE for x, y in zip(a, b))


# B-BBEE level -> points (Preferential Procurement Regulations 2017, SBD 6.1 section 4.1) and -> recognition level
POINTS_80_20 = {1: 20, 2: 18, 3: 14, 4: 12, 5: 8, 6: 6, 7: 4, 8: 2}
POINTS_90_10 = {1: 10, 2: 9, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}
RECOGNITION = {1: 135, 2: 125, 3: 110, 4: 100, 5: 80, 6: 60, 7: 50, 8: 10}
PAGE_NO = re.compile(r"\bp?a?ge\W{0,3}(\d{1,2})\s*of\s*(\d{1,2})\b", re.I)

GENERIC_ADDRESS_WORDS = {"road", "rd", "street", "st", "str", "avenue", "ave", "drive", "dr", "the"}


@dataclass
class Evidence:
    page: int
    note: str = ""
    box: tuple | None = None
    source: str = "ocr"  # ocr | manual | vision | image
    pad: tuple = (40, 30, 40, 30)  # left, top, right, bottom padding when the report crops this box


@dataclass
class Finding:
    id: str
    severity: str  # high | medium | low | info
    category: str
    title: str
    detail: str
    action: str
    rule: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    tier: str = "issue"  # issue = a rule or a cross-document check fails | observation = worth knowing, not a failure


@dataclass
class Passed:
    title: str
    note: str = ""
    pages: list[int] = field(default_factory=list)


@dataclass
class AuditResult:
    findings: list[Finding] = field(default_factory=list)
    passed: list[Passed] = field(default_factory=list)
    ocr_variants: list[str] = field(default_factory=list)
    human_only: list[str] = field(default_factory=list)
    page_types: dict[int, str] = field(default_factory=dict)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def mask(v: str) -> str:
    v = str(v)
    return v if len(v) <= 6 or "@" in v else v[:3] + "•" * (len(v) - 5) + v[-2:]


def luhn_ok(num: str) -> bool:
    if not re.fullmatch(r"\d{13}", num):
        return False
    total = 0
    for i, ch in enumerate(reversed(num)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def norm_name(s: str) -> str:
    s = re.sub(r"\(?pty\)?|\bltd\b|\blimited\b", "", s.lower())
    return re.sub(r"[^a-z]", "", s)


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], 1)}


def parse_dmy(s: str) -> date | None:
    m = re.match(r"(\d{1,2}) (\w{3})\w* (\d{4})", s)
    if m:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def iso(s: str) -> date | None:
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except Exception:
        return None


class Auditor:
    def __init__(self, req: Requirements, pages: list[Page], facts: Facts, blank_dir: Path,
                 unmatched: dict | None = None):
        self.req, self.pages, self.f = req, pages, facts
        self.res = AuditResult(page_types={p.number: p.doc_type for p in pages})
        self.blank = {Path(p).stem: Path(p).read_text() for p in glob.glob(str(blank_dir / "*.txt"))}
        self.vocab = V.build_vocab(list(self.blank.values()))
        self.by_no = {p.number: p for p in pages}
        self.unmatched = unmatched or {}  # handwriting transcribed from pages that are gone or altered
        self.templates = load_templates(blank_dir)
        self._n = 0

    # -- helpers -----------------------------------------------------------
    def pg(self, dtype) -> list[Page]:
        return [p for p in self.pages if p.doc_type == dtype]

    def pn(self, dtype, i=0):
        """Page number (in THIS pack) of the i-th page of a form, or None if the form is not in the pack."""
        pgs = self.pg(dtype)
        return pgs[i].number if -len(pgs) <= i < len(pgs) else None

    def pns(self, *dtypes) -> list[int]:
        return [p.number for t in dtypes for p in self.pg(t)]

    def fp(self, *fields) -> list[int]:
        return sorted({self.f.best(x).page for x in fields if self.f.best(x)})

    def E(self, page, note="", box=None, source="ocr", pad=(40, 30, 40, 30)):
        return Evidence(page, note, box, source, pad) if page else None

    def find(self, sev, cat, title, detail, action, rule="", ev=None, tier=None):
        self._n += 1
        tier = tier or ("issue" if sev in ("high", "medium") else "observation")
        self.res.findings.append(Finding(f"F{self._n:02d}", sev, cat, title, detail, action, rule,
                                         [e for e in (ev or []) if e], tier))

    def ok(self, title, note="", pages=None):
        self.res.passed.append(Passed(title, note, [p for p in (pages or []) if p]))

    # -- run ----------------------------------------------------------------
    def run(self) -> AuditResult:
        for fn in (self.required_documents, self.page_completeness, self.page_order, self.signatures,
                   self.handwritten_entries, self.signatories, self.vision_inspection, self.template_blanks,
                   self.sbd62_consistency, self.local_content, self.identifiers, self.names, self.quote_checks,
                   self.spec_checks, self.validity_dates, self.declarations_vs_registers, self.certificates,
                   self.bbbee, self.stray_marks, self.initials, self.ocr_quality, self.not_verifiable):
            fn()
        order = {"issue": 0, "observation": 1}
        self.res.findings.sort(key=lambda x: (order[x.tier], SEV_ORDER[x.severity], x.id))
        return self.res

    # -- 1. required documents ---------------------------------------------
    def required_documents(self):
        present = {p.doc_type for p in self.pages}
        if "quote" in present:
            self.ok("Quotation present", pages=self.pns("quote"))
        else:
            self.find("high", "missing_document", "Missing: the quotation",
                      "No page was recognised as the supplier's quotation (item, quantity, price, validity, signature).",
                      "Check that the quotation is in the pack.", "RFQ: quotations must be submitted")
        mapping = {"tcc": "tcs", "bbbee_certificate": "bbbee_affidavit", "sbd4": "sbd4", "sbd8": "sbd8",
                   "sbd9": "sbd9", "sbd6_1": "sbd6_1", "sbd6_2": "sbd6_2", "csd_report": "csd"}
        for d in self.req.documents:
            if d.id == "annexure_c":
                hit = [p for p in self.pages if p.doc_type not in ("sbd6_2", "local_content_decl")
                       and re.search(r"ANNEX(URE)?\s*C\b", p.text, re.I)]
                if hit:
                    self.ok("Annexure C present", pages=[p.number for p in hit])
                elif not self.req.local_content_required:
                    self.ok("Annexure C not needed: no local content requirement",
                            f"RFQ: only 'where there is local content'. Basis: {self.req.local_content_basis}.")
                else:
                    self.find(
                        "high", "missing_document",
                        "Annexure C (Local Content Declaration: Summary Schedule) is not in the pack",
                        "The RFQ lists Annexure C where there is local content, and local content applies to this bid "
                        f"({self.req.local_content_basis}). The pack has no Annexure C schedule.",
                        "Confirm whether Annexure C was meant to be included.",
                        f"RFQ: \"{d.source_quote[:120]}...\"; SBD 6.2 clause 1.6(a)",
                        [self.E(self.pn("sbd6_2", 1), "SBD 6.2 clause 1.6")])
                continue
            dtype = mapping.get(d.id)
            if dtype == "sbd6_2" and dtype not in present and not self.req.local_content_required:
                self.ok("SBD 6.2 not needed: no local content requirement")
            elif dtype and dtype in present:
                self.ok(f"Required document present: {d.label}", pages=self.pns(dtype))
            elif dtype:
                self.find("high", "missing_document", f"Missing: {d.label}", "Required by the RFQ but no page was "
                          "recognised as this document.", "Check the pack for this document.",
                          f"RFQ: \"{d.source_quote[:100]}\"")

    def page_completeness(self):
        for dtype, expect in TEMPLATE_PAGES.items():
            pgs = self.pg(dtype)
            got = len(pgs)
            if got == expect:
                self.ok(f"{DOC_LABEL[dtype]}: all {expect} pages present", pages=[p.number for p in pgs])
            elif got > expect:
                self.find("medium", "incomplete_form", f"{DOC_LABEL[dtype]}: {got} pages found, the standard form has {expect}",
                          "More pages than the form as issued. A page may be duplicated or something may have been inserted.",
                          "Check the pages listed.", "Standard form page count",
                          [self.E(p.number) for p in pgs][:4])
            elif got:
                self.find("medium", "incomplete_form", f"{DOC_LABEL[dtype]}: {got} of {expect} pages found",
                          "Fewer pages than the standard form. A page of this form may be missing.",
                          "Check the form for the missing page.", "Standard form page count",
                          [self.E(p.number) for p in pgs][:4])
        for p in self.pg("blank"):
            self.find("medium", "incomplete_form", f"Pack page {p.number} is blank",
                      "No printed text was found on this page. A blank sheet inside a bid pack is unusual: it can replace "
                      "a removed page or hide something that was taken out.",
                      "Check whether a page is missing here, or remove the blank sheet.", "",
                      [self.E(p.number, "blank page", source="image")])
        csd = self.pg("csd")
        if csd:
            text = "\n".join(p.text for p in csd)
            need = {"supplier identification": r"SUPPLIER\s+IDEN\w*", "commodity list": r"SUPPLIER\s+COMMODIT",
                    "B-BBEE information": r"BBBEE\s+INFORMATION", "tax status": r"Tax\s+Compli"}
            gone = [k for k, rx in need.items() if not re.search(rx, text, re.I)]
            if gone:
                self.find("medium", "incomplete_form", "The CSD summary report is missing a section",
                          "Not found in the CSD pages: " + ", ".join(gone) + ". A page of the report may be missing.",
                          "Check the CSD report is complete.", "RFQ: latest CSD summary report",
                          [self.E(p.number) for p in csd][:4])
            else:
                self.ok("CSD summary report has all its sections", "identification, commodities, B-BBEE, tax",
                        [p.number for p in csd])
        a, b = self.pg("sbd6_2"), self.pg("local_content_decl")
        if bool(a) != bool(b):
            have, lack = ("SBD 6.2", "its local content declaration (Annexure B)") if a else \
                ("The local content declaration", "SBD 6.2")
            self.find("medium", "incomplete_form", f"{have} is in the pack but {lack} is not",
                      "These two forms go together. One of them may have been removed.", "Check the pack.",
                      "SBD 6.2 clause 1.6", [self.E(p.number) for p in (a or b)][:3])
        seen: dict[str, list[int]] = {}
        for p in self.pages:
            seen.setdefault(p.sha, []).append(p.number)
        dups = [v for v in seen.values() if len(v) > 1]
        for v in dups:
            self.find("medium", "incomplete_form", f"The same page appears more than once (pack pages {v})",
                      "These page images are identical, which usually means a page was copied in twice.",
                      "Remove the duplicate or confirm it is intended.", "", [self.E(n, source="image") for n in v][:2])
        if not dups:
            self.ok("No page appears twice")

    # -- 2. signatures -------------------------------------------------------
    def signatures(self):
        spec = V.SIGNATURE_SPEC
        for dtype, (idx, label, fallback, lay) in spec.items():
            pgs = self.pg(dtype)
            if not pgs:
                continue
            p = pgs[idx]
            ev = V.signature_evidence(p, label, lay) or (V.signature_evidence(p, fallback, lay) if fallback else [])
            ev = sorted(ev, key=lambda e: e["label_box"][1])[-1:] if ev else []
            if not ev:
                self.find("medium", "check_crop", f"Could not locate the signature line on {DOC_LABEL[dtype]}",
                          "The page expected to carry the signature (the last page of the form) has no 'Signature' label. "
                          "The signing page may be missing.",
                          "Look at the form to confirm the signing page is there and signed.", "",
                          [self.E(p.number, source="image")])
            elif ev[0]["present"]:
                self.ok(f"{DOC_LABEL[dtype]} signed (pen strokes found on the signature line)", pages=[p.number])
                vm = next((f for f in self.f.all("inspect.signature_missing") if f.page == p.number), None)
                if vm:
                    self.find("medium", "unsigned", f"{DOC_LABEL[dtype]}: the mark on the signature line may not be a signature",
                              "Ink was found on the signature line, but a vision check of the page does not see a pen "
                              "signature there (it could be a typed name, a scribble or a stamp).",
                              "Look at the crop and confirm it is a real signature.", "Form certification clause",
                              [self.E(p.number, box=ev[0]["zone"], source="vision")], tier="observation")
            else:
                self.find("high", "unsigned", f"{DOC_LABEL[dtype]} may be unsigned",
                          "No pen strokes were found above the signature line.",
                          "Open the page and confirm a signature is present.", "Form certification clause",
                          [self.E(p.number, box=ev[0]["zone"], source="image")])
        for dtype in ("local_content_decl", "sbd6_1"):
            n = self.f.value(f"{dtype}.witness_signatures")
            if isinstance(n, (int, float)) and int(n) >= 2:
                f = self.f.best(f"{dtype}.witness_signatures")
                self.ok(f"{DOC_LABEL[dtype]}: two witness signatures", f"(source: {f.source})", [f.page])

    # -- page order ------------------------------------------------------------
    def page_order(self):
        """Are the pages where they belong? Three independent signs: a form's pages sit together; the 'page n of m'
        footers (where OCR can read them) count upwards; and each page's wording sits later in the blank form than
        the page before it. Needs no model."""
        seq = [p for p in self.pages if p.doc_type not in ("blank", "unknown")]
        runs: dict[str, list[list[Page]]] = {}
        prev = None
        for p in seq:
            if p.doc_type != prev:
                runs.setdefault(p.doc_type, []).append([])
            runs[p.doc_type][-1].append(p)
            prev = p.doc_type
        problems = 0
        for dtype, rs in runs.items():
            label = DOC_LABEL.get(dtype, dtype)
            if len(rs) > 1:
                problems += 1
                nums = [[p.number for p in r] for r in rs]
                self.find("medium", "page_order", f"{label}: its pages are not together",
                          f"The pages of this form appear in separate places in the pack (pack pages {nums}). A page may "
                          "have been moved, or another document may sit in the middle of it.",
                          "Check the order of the pages listed.", "",
                          [self.E(r[0].number, source="image") for r in rs][:4])
                continue
            pgs = rs[0]
            if len(pgs) < 2:
                continue
            msg, pair = None, None
            foot = [(p, int(m.group(1))) for p in pgs for m in [PAGE_NO.search(p.text)] if m and 1 <= int(m.group(1)) <= 12]
            for (a, na), (b, nb) in zip(foot, foot[1:]):
                if nb < na:
                    msg = (f"Pack page {a.number} is marked 'page {na}' and the next page, pack page {b.number}, is "
                           f"marked 'page {nb}'.")
                    pair = (a, b)
                    break
            t = self.templates.get(dtype)
            if not msg and t:
                loc = [(p, *t.locate(p.text)) for p in pgs]
                loc = [(p, pos, n) for p, pos, n in loc if pos is not None and n >= 8]
                for (a, pa, _), (b, pb, _) in zip(loc, loc[1:]):
                    if pb < pa - 0.03:
                        msg = (f"The wording on pack page {b.number} comes earlier in the blank {label} than the wording "
                               f"on the page before it (pack page {a.number}).")
                        pair = (a, b)
                        break
            if msg:
                problems += 1
                self.find("medium", "page_order", f"{label}: pages are out of order", msg + " The form's pages may have "
                          "been shuffled.", "Put the pages in the order the form was issued.", "Standard form page order",
                          [self.E(pair[0].number, source="image"), self.E(pair[1].number, source="image")])
        csd = self.pg("csd")
        if len(csd) > 1:
            marks = [("identification", r"SUPPLIER\s+IDEN"), ("directors", r"DIRECTOR\s*/\s*MEMBERS|SUPPLIER\s*\n?\s*DIRECTOR"),
                     ("commodities", r"SUPPLIER\s+COMMODIT"), ("B-BBEE", r"BBBEE\s+INFORMATION")]
            first = {k: next((i for i, p in enumerate(csd) if re.search(rx, p.text, re.I)), None) for k, rx in marks}
            seen = [(k, first[k]) for k, _ in marks if first[k] is not None]
            for (k1, i1), (k2, i2) in zip(seen, seen[1:]):
                if i2 < i1:
                    problems += 1
                    self.find("medium", "page_order", "CSD summary report: sections are out of order",
                              f"The {k2} section sits on an earlier page than the {k1} section.",
                              "Put the report's pages in order.", "", [self.E(csd[i1].number, source="image"),
                                                                       self.E(csd[i2].number, source="image")])
                    break
        if not problems:
            self.ok("Pages are in order: each form's pages sit together and run in sequence",
                    "page-number footers, the blank forms' wording, and the CSD sections agree", [p.number for p in seq][:1])

    # -- handwritten entries (ticks, names, dates, amounts, stamps) ---------------------
    def _hand(self, key):
        f = self.f.best(key)
        return f if f and f.source in ("manual", "vision") else None

    # These SBD 4 yes/no questions have no focused crop check (facts.verify_yesno): their printed number has
    # no inline anchor on the same line as their own "YES / NO" (checked directly against samples/blank, not
    # assumed). A demonstrated case (question 2.8) showed a whole-page reading of a small tick is not
    # reliable enough to trust even when the model's own whole-page second look agrees with itself - so these
    # three never reach "issue" tier, however confident the whole-page reading claims to be.
    _NO_CROP_COVERAGE = {"sbd4.q2_7_2_outside_work_authority", "sbd4.q2_8_business_with_state_12m",
                        "sbd4.q2_9_relationship_with_evaluator"}

    @classmethod
    def _tier(cls, f):
        """A reading is an issue when a person gave it or a second vision look confirmed it; otherwise it is only
        something to look at, because a single model reading is not proof."""
        if f.field in cls._NO_CROP_COVERAGE:
            return "observation"
        return "issue" if f.confidence >= 0.8 else "observation"

    def handwritten_entries(self):
        local = self.req.local_content_required
        if not any(f.source in ("manual", "vision") and f.field in ENTRY for f in self.f.items):
            self.res.human_only.append(
                "Handwritten entries were NOT read: no vision model is connected and no transcript was supplied. That "
                "covers the ticks on SBD 4 and SBD 8, handwritten names, dates, amounts and the commissioner's stamp. "
                "Connect Llama vision (see OLLAMA.md) and these checks run automatically.")
            return
        for dtype, entries in F.FORMS.items():
            if not self.pg(dtype):
                continue
            form = DOC_LABEL[dtype]
            done, bad, unread, pages = 0, 0, [], set()
            for e in entries:
                if e.applies == "local_content" and not local:
                    continue
                f = self._hand(e.key)
                if f is None:
                    if e.required:
                        unread.append(e.label)
                    continue
                done += 1
                pages.add(f.page)
                v, ev = f.value, [self.E(f.page, e.label, source=f.source)]
                how = {"manual": "transcribed by a person", "vision": "read by the vision model"}[f.source]
                if v is None:
                    if e.required:
                        bad += 1
                        self.find("high" if e.kind == F.YESNO else "medium", "incomplete_form",
                                  f"{form}: {e.label} is blank",
                                  f"Nothing is written or ticked for the {e.label} (page {f.page}, {how}). The form has to "
                                  "be completed in full.", "Complete the entry and re-sign if needed.",
                                  "Standard form must be completed in full", ev, tier=self._tier(f))
                elif v == "Both":
                    bad += 1
                    self.find("medium", "inconsistency", f"{form}: both YES and NO are marked for the {e.label}",
                              f"Both boxes are marked (page {f.page}, {how}). The answer is unclear.",
                              "Ask the bidder to mark one answer and initial the correction.",
                              "Standard form must be completed in full", ev, tier=self._tier(f))
                elif e.kind == F.YESNO and v == "Yes" and e.expect != "Yes":
                    bad += 1
                    sev = "high" if dtype == "sbd8" else "medium"
                    if e.expect is None:
                        title, detail = (f"{form}: {e.label} is answered Yes",
                                         "Subcontracting is declared. The form then asks for the percentage and the "
                                         "subcontractor's details, which this check cannot see.")
                    else:
                        title, detail = (f"{form}: {e.label} is answered Yes",
                                         "The clean answer is No. A Yes needs particulars on the form and can change "
                                         "whether the bid is accepted; the particulars are not checked here.")
                    self.find(sev, "declaration", title, f"{detail} (page {f.page}, {how})",
                              "Confirm the answer is intended and that the particulars are given.", form, ev,
                              tier=self._tier(f))
                elif e.kind == F.YESNO and e.expect == "Yes" and v == "No":
                    bad += 1
                    self.find("high", "unsigned", f"{form}: no {e.label}",
                              f"The {e.label} is missing (page {f.page}, {how}). A sworn affidavit needs the deponent's "
                              "signature and the commissioner's stamp and signature.",
                              "Get the document properly signed and commissioned.", form, ev, tier=self._tier(f))
                elif e.kind == F.COUNT and e.key.endswith("witness_signatures") and isinstance(v, (int, float)) and v < 2:
                    bad += 1
                    self.find("medium", "unsigned", f"{form}: only {int(v)} witness signature(s)",
                              f"The form needs two witnesses ({how}).", "Get the missing witness signature(s).", form, ev,
                              tier=self._tier(f))
            if unread:
                self.res.human_only.append(f"{form}: could not be read: {', '.join(unread)}.")
            if done and not bad:
                self.ok(f"{form}: all {done} handwritten entries are filled in and none gives cause for concern",
                        "answers, names, dates and amounts as read" + (f"; {len(unread)} could not be read" if unread else ""),
                        sorted(pages))

    def signatories(self):
        sources = [("quote.signatory", "Quotation"), ("local_content_decl.declarant", "Local content declaration"),
                   ("sbd4.declarant", "SBD 4"), ("sbd8.declarant", "SBD 8"), ("sbd4.director_table_name", "SBD 4 director table"),
                   ("bbbee_affidavit.deponent", "B-BBEE affidavit"), ("cipc.director", "CIPC certificate")]
        people = [(lab, self.f.best(k)) for k, lab in sources if self.f.best(k) and self.f.best(k).value]
        if len(people) < 2:
            return

        def toks(n):
            return {t for t in re.findall(r"[a-z]+", str(n).lower()) if len(t) > 1}

        def close(a, b):
            return a == b or (min(len(a), len(b)) >= 5 and edit_distance(a, b) <= 1)

        def same(a, b):
            ta, tb = toks(a), toks(b)
            hits = sum(1 for x in ta if any(close(x, y) for y in tb))
            return hits >= min(2, len(ta), len(tb))

        ref = next(((lab, f) for lab, f in people if lab == "CIPC certificate"), None)
        if ref is None:
            top = Counter(norm_name(f.value) for _, f in people).most_common(1)[0][0]
            ref = next((lab, f) for lab, f in people if norm_name(f.value) == top)
        off = [(lab, f) for lab, f in people if not same(f.value, ref[1].value)]
        if off:
            # a disagreement is only an issue when every name in it is printed/typed text (OCR); a name read
            # from handwriting by the vision model can be misread, so that disagreement is a lead, not a fact
            all_ocr = ref[1].source == "ocr" and all(f.source == "ocr" for _, f in off)
            self.find("medium", "inconsistency", "The person who signed differs between documents",
                      f"{ref[0]} gives '{ref[1].value}'. Different: " + "; ".join(f"{lab}: '{f.value}'" for lab, f in off),
                      "Check who signed each document; the signatory should be the director or authorised person.",
                      "Cross-document consistency",
                      [self.E(f.page, lab, source=f.source) for lab, f in [ref] + off][:5],
                      tier="issue" if all_ocr else "observation")
        else:
            self.ok(f"Everyone who signed or swore is the same person ({ref[0]} agrees)",
                    pages=sorted({f.page for _, f in people}))

    # -- 3. template blanks: bidder vs buyer ---------------------------------
    def template_blanks(self):
        blank61 = next((t for k, t in self.blank.items() if "6_1" in k), "")
        buyer_side = bool(re.search(r"exceed/not exceed", blank61))
        hl = [r for p in self.pg("sbd6_1") for r in V.highlight_remnants(p)]
        p3 = next((p for p in self.pg("sbd6_1") if re.search(r"exceed\s*/\s*not exceed", p.text, re.I)), None)
        if p3 or hl:
            if buyer_side:
                self.ok("SBD 6.1 sections 1.2 and 1.4 left as supplied by the buyer",
                        "Their placeholders and yellow highlighting are also in the blank SBD 6.1 attached to the RFQ, "
                        "so these are buyer-side fields, not omissions by the bidder.",
                        [p3.number] if p3 else [])
            else:
                self.find("medium", "incomplete_form", "SBD 6.1 sections 1.2/1.4 left unfilled",
                          "Placeholders remain although the blank form had them filled.",
                          "Check whether the bidder was meant to complete these.", "SBD 6.1 1.2/1.4",
                          [self.E((p3 or self.pg("sbd6_1")[0]).number, source="image")])
        pts = self.f.value("sbd6_1.points_claimed")
        lvl = self.f.value("bbbee_affidavit.level")
        if isinstance(pts, (int, float)) and isinstance(lvl, int):
            f = self.f.best("sbd6_1.points_claimed")
            total = self.f.value("quote.total") or self.f.value("local_content_decl.bid_price_excl_vat") or 0
            system, table = ("80/20", POINTS_80_20) if total <= 50_000_000 else ("90/10", POINTS_90_10)
            want = table.get(lvl, 0)
            if int(pts) == want:
                self.ok(f"B-BBEE points claimed ({int(pts)}) match Level {lvl} under the {system} table",
                        f"claim read via {f.source}" + (f", confidence {f.confidence:.0%}" if f.source == "vision" else ""),
                        [f.page])
            else:
                self.find("medium", "inconsistency", "Points claimed do not match the B-BBEE level",
                          f"Claimed {int(pts)} points; the affidavit gives Level {lvl}, which earns {want} points under "
                          f"the {system} system (bid value R{total:,.0f}).", "Check the claim against the affidavit.",
                          "SBD 6.1 section 4.1 table", [self.E(f.page, "points claimed", source=f.source)],
                          tier=self._tier(f))

    # -- 4. SBD 6.2 internal consistency --------------------------------------
    def sbd62_consistency(self):
        tmpl = self.f.best("sbd6_2.templates_audited_certified")
        decl_pages = self.pg("local_content_decl")
        decl_says = any(re.search(r"audited and certified to be correct", p.text, re.I) for p in decl_pages)
        if tmpl and str(tmpl.value).lower() == "no":
            if self.req.local_content_required and decl_says:
                decl_p = next(p for p in decl_pages if re.search(r"audited and certified to be correct", p.text, re.I))
                self.find(
                    "high", "inconsistency",
                    "SBD 6.2 says the local content templates were NOT audited, but the signed declaration says they were",
                    "SBD 6.2 paragraph 5 is ticked No while the declaration states in (b)(ii) that the templates were "
                    "audited and certified. Local content applies to this bid, so this matters (clause 1.6(b)).",
                    "Ask the bidder which statement is right.",
                    "SBD 6.2 paragraph 5 and clause 1.6(b); Local content declaration (b)(ii)",
                    [self.E(tmpl.page, "paragraph 5 ticked No", source=tmpl.source),
                     self.E(decl_p.number, "declaration (b)(ii)", source="ocr")])
            elif not self.req.local_content_required:
                self.ok("SBD 6.2 paragraph 5 ticked No (templates not audited), consistent with no local content requirement",
                        pages=[tmpl.page])
        imp = self.f.best("sbd6_2.has_imported_content")
        impv = self.f.value("local_content_decl.imported_content")
        if imp and str(imp.value).lower() == "no" and impv in (0, None):
            self.ok("SBD 6.2 'no imported content' agrees with the declaration (imported content R0)",
                    pages=[imp.page])

    # -- 5. local content ------------------------------------------------------
    def local_content(self):
        price = self.f.value("local_content_decl.bid_price_excl_vat")
        imported = self.f.value("local_content_decl.imported_content", 0)
        pct = self.f.value("local_content_decl.local_content_pct")
        thr = self.f.value("sbd6_2.min_threshold_pct")
        if price:
            calc = (1 - imported / price) * 100
            pages = self.fp("local_content_decl.bid_price_excl_vat")
            if pct is not None and abs(calc - float(pct)) < 0.01:
                self.ok(f"Local content arithmetic: [1 - {imported}/{int(price)}] x 100 = {calc:.0f}%",
                        "SATS 1286:2011 formula, as printed on SBD 6.2", pages)
            elif pct is not None:
                self.find("high", "arithmetic", "Local content percentage does not follow from the declared figures",
                          f"[1 - {imported}/{int(price)}] x 100 = {calc:.1f}% but {pct}% is declared.",
                          "Check the declaration.", "SBD 6.2 formula (SATS 1286:2011)",
                          [self.E(p, source="manual") for p in pages])
            if thr is not None and pct is not None and float(pct) >= float(thr) and self.req.local_content_required:
                self.ok(f"Declared local content ({pct}%) meets the stipulated threshold ({thr}%)",
                        pages=self.fp("sbd6_2.min_threshold_pct") + pages)
        cls = self.f.value("sbd6_1.classification_ticked")
        if cls == "Supplier" and pct == 100 and self.req.local_content_required:
            self.find(
                "medium", "verify", "100% local content declared by a bidder classed as a Supplier (not a manufacturer)",
                "SBD 6.1 section 8.6 classifies the bidder as a Supplier. SBD 6.2 defines local content as the part "
                "of the price not imported, provided that local manufacture does take place. A reseller declaring "
                "100% with R0 imported content is plausible only if the product is made locally by someone else, "
                "and that is not evidenced in the pack.",
                "Be ready to show the manufacturer's evidence of local manufacture. The declaration says the buyer "
                "may request verification.",
                "SBD 6.2 definitions 2.6/2.7; local content declaration paragraph (d)",
                [self.E(self.f.best("sbd6_1.classification_ticked").page, "classification: Supplier", source="manual"),
                 self.E(self.f.best("local_content_decl.local_content_pct").page, "local content 100%, imported R0",
                        source="manual")])

    # -- 6. identifiers --------------------------------------------------------
    _WHITELIST = {"Company registration number": "0123456789/", "CSD supplier number": "MA0123456789",
                  "Tax reference number": "0123456789", "SA ID number": "0123456789"}

    def _settle(self, label, consensus, v, f, n_cons, cons_facts):
        """A value that differs from the consensus by 1-2 characters: is it a scanner misread or a real change?
        Re-read both sides at higher magnification (two magnifications must agree). The scanner-confusion shortcut
        applies only when the consensus rests on at least two pages; a 1-v-1 split cannot be decided by majority."""
        wl = self._WHITELIST.get(label)
        if wl:
            norm = lambda x: x.upper().replace(" ", "")
            rr_v = V.reread(self.by_no[f.page], f.box, wl) if f.source == "ocr" and f.box else ""
            cf = next((c for c in cons_facts if c.source == "ocr" and c.box), None)
            rr_c = V.reread(self.by_no[cf.page], cf.box, wl) if cf else ""
            if norm(rr_v) == consensus.upper() or (rr_c and norm(rr_c) == v.upper()):
                return "same"
            if rr_v and norm(rr_v) == v.upper():
                return "different"
        if n_cons >= 2 and f.source == "ocr" and only_confusable_diff(v.upper(), consensus.upper()):
            return "scanner"
        return "unexplained"

    def identifiers(self):
        pools = {
            "Company registration number": ["id.reg_no", "sbd4.reg_no", "sbd6_1.reg_no"],
            "CSD supplier number": ["id.csd_no"],
            "Tax reference number": ["id.tax_no", "sbd4.tax_no"],
            "SA ID number": ["id.sa_id", "sbd4.id_no"],
            "Email address": ["id.email"],
        }
        for label, fields in pools.items():
            vals = [(f.value.replace(" ", ""), f) for fld in fields for f in self.f.all(fld)]
            if not vals:
                continue
            if label == "Email address":
                vals = [(v.lower(), f) for v, f in vals]
            # the reference value comes from printed/typed text (OCR) whenever any exists - a handwritten
            # reading (vision) must never outvote it just because more pages happen to carry it
            ocr_vals = [(v, f) for v, f in vals if f.source == "ocr"]
            pool = ocr_vals or vals
            counts = Counter(v for v, _ in pool)
            consensus, n = counts.most_common(1)[0]
            pages = sorted({f.page for v, f in vals if v == consensus})
            others = [(v, f) for v, f in vals if v != consensus]
            far = [(v, f) for v, f in others if edit_distance(v, consensus) > 2]
            for v, f in [(v, f) for v, f in others if edit_distance(v, consensus) <= 2]:
                if label == "Email address":
                    # a supplier's own address is not something a bid is judged on; OCR often drops or swaps letters
                    verdict = "scanner" if n >= 2 else "observe"
                else:
                    verdict = self._settle(label, consensus, v, f, n, [c for u, c in vals if u == consensus])
                if verdict == "same":
                    self.res.ocr_variants.append(
                        f"{label}: page {f.page} first read as {mask(v)}; a second read at higher magnification gives "
                        f"{mask(consensus)}, the same as elsewhere. Scanner misread, no action.")
                elif verdict == "scanner":
                    self.res.ocr_variants.append(
                        f"{label}: page {f.page} reads {mask(v)} vs {mask(consensus)} elsewhere ({f.source}). The "
                        "characters that differ are ones this scanner resolution confuses, and a second read could not "
                        "settle it. Treated as a misread; compare the crop if it matters.")
                elif verdict == "observe":
                    self.find("low", "check_crop", "Email address read two ways",
                              f"{label}: {v} on page {f.page} vs {consensus} on {pages}. They differ by a character or two "
                              "and only one page backs each reading.", "Compare the crops; probably a scanner misread.",
                              "Cross-document consistency", [self.E(f.page, source=f.source, box=f.box)],
                              tier="observation")
                else:
                    far.append((v, f))
            if far:
                # a handwritten (vision) reading can be misread, so a mismatch it is part of is a lead, not a
                # fact - only a mismatch where every differing page is printed/typed text (OCR) is an issue
                all_ocr = all(f.source == "ocr" for v, f in far)
                self.find("medium", "inconsistency", f"{label} differs between documents",
                          f"Most pages agree on {mask(consensus)} (pages {pages}). Different values: "
                          + "; ".join(f"{mask(v)} on page {f.page}" for v, f in far),
                          "Open the pages and compare. If it is a misread, ignore; if not, correct the form.",
                          "Cross-document consistency",
                          [self.E(f.page, source=f.source, box=f.box) for v, f in far],
                          tier="issue" if all_ocr else "observation")
            elif len({f.page for _, f in vals}) >= 2:
                self.ok(f"{label} consistent across {len({f.page for _, f in vals})} pages",
                        f"{mask(consensus)}", pages)
            if label == "SA ID number":
                if luhn_ok(consensus):
                    self.ok("SA ID number passes the checksum", f"{mask(consensus)}", pages)
                else:
                    self.find("low", "check_crop", "SA ID number fails the checksum",
                              "The ID read from the pages does not pass the standard check digit test. This is "
                              "often an OCR or handwriting misread.", "Compare against the ID document.", "",
                              [self.E(pages[0])])
        csd_pages = sorted({f.page for f in self.f.all("id.csd_no")})
        if len({f.value for f in self.f.all("id.csd_no")}) == 1 and len(csd_pages) >= 2:
            self.ok("CSD number on the quote matches the CSD report", pages=csd_pages)

    def names(self):
        fields = ["bbbee_affidavit.enterprise_name", "cipc.enterprise_name", "sbd6_1.company_name",
                  "sbd4.name_field", "local_content_decl.bidder", "sbd9.bidder", "tcs.taxpayer_name", "csd.legal_name"]
        items = [(f.value, f) for fld in fields for f in self.f.all(fld)]
        if not items:
            return
        norms = {norm_name(v) for v, _ in items}
        raw = sorted({v for v, _ in items})
        pages = sorted({f.page for _, f in items})
        if len(norms) == 1:
            self.ok("Company name consistent across documents",
                    ("written as " + "; ".join(f"'{r}'" for r in raw)) if len(raw) > 1 else "", pages)
        else:
            self.find("medium", "inconsistency", "Company name differs between documents", "; ".join(raw),
                      "Compare with the CIPC certificate.", "",
                      [self.E(f.page, f"'{v}'", source=f.source) for v, f in items][:6])

    # -- 7. quote ----------------------------------------------------------------
    def quote_checks(self):
        qp = self.pn("quote")
        up, tot = self.f.value("quote.unit_price"), self.f.value("quote.total")
        qty_q = self.f.value("quote.quantity")
        qty_s = self.req.spec.get("quantity")
        if qty_q is not None and qty_s:
            if int(qty_q) == int(qty_s):
                self.ok(f"Quantity on the quote ({qty_q}) matches the specification", pages=self.fp("quote.quantity"))
            else:
                self.find("high", "spec_mismatch", "Quantity on the quote differs from the specification",
                          f"The quote offers {qty_q}; the specification asks for {qty_s}.",
                          "Correct the quantity or confirm the change with the buyer.", "Specification sheet",
                          [self.E(qp, "quantity on the quote", self.f.best("quote.quantity").box),
                           self.E(self.pn("spec"), "specification", source="ocr")])
        n = qty_q if qty_q is not None else qty_s
        if up and tot and n:
            if abs(up * n - tot) < 0.5:
                self.ok(f"Quote arithmetic: {n} x R{up:,.2f} = R{tot:,.2f}", pages=self.fp("quote.total"))
            else:
                self.find("high", "arithmetic", "Quote total does not equal quantity x unit price",
                          f"{n} x {up:,.2f} = {up * n:,.2f} but the total is {tot:,.2f}.",
                          "Check the quote.", "",
                          [self.E(qp, "total", self.f.best("quote.total").box),
                           self.E(qp, "quantity and unit price", self.f.best("quote.unit_price").box)])
        lc = self.f.value("local_content_decl.bid_price_excl_vat")
        if lc and tot and abs(lc - tot) < 0.5:
            self.ok("Bid price on the local content declaration equals the quote total", f"R{tot:,.0f}",
                    self.fp("quote.total", "local_content_decl.bid_price_excl_vat"))
        elif lc and tot:
            self.find("high", "inconsistency", "Bid price differs between quote and local content declaration",
                      f"{lc:,.2f} vs {tot:,.2f}", "Check both.", "",
                      [self.E(qp, "quote total", self.f.best("quote.total").box),
                       self.E(self.fp("local_content_decl.bid_price_excl_vat")[0], "price on the declaration",
                              source="manual")])
        # reference
        ref = self.f.best("quote.reference")
        rfq_ref = self.req.reference
        if ref and rfq_ref:
            norm = lambda x: re.sub(r"\s", "", x)
            if norm(ref.value) == norm(rfq_ref):
                self.ok("Quote reference matches the RFQ", pages=[ref.page])
            else:
                rr = V.reread(self.by_no[ref.page], ref.box, "0123456789/()") if ref.box else ""
                box = (ref.box[0], ref.box[1], ref.box[2] + 20, ref.box[3]) if ref.box else None
                if rr == norm(rfq_ref):
                    self.ok("Quote reference matches the RFQ", "first OCR pass misread one character; a second read "
                            "at higher magnification agrees with the RFQ", [ref.page])
                elif rr and rr != norm(rfq_ref):
                    self.find("high", "inconsistency", "Quote reference differs from the RFQ",
                              f"The quote reads '{rr}'; the RFQ says '{rfq_ref}'.",
                              "Correct the reference on the quote.", "RFQ subject line",
                              [self.E(ref.page, "quote reference line", box, "ocr", pad=(40, 20, 40, 20))])
                elif edit_distance(ref.value, rfq_ref) <= 2 and only_confusable_diff(norm(ref.value), norm(rfq_ref)):
                    self.res.ocr_variants.append(
                        f"Quote reference: OCR reads '{ref.value}' where the RFQ says '{rfq_ref}'. The differing "
                        "character is one this scan resolution confuses (1 and 4), and a second read could not settle it. "
                        "Treated as a misread; check the crop if it matters.")
                else:
                    self.find("high", "inconsistency", "Quote reference differs from the RFQ",
                              f"'{ref.value}' vs '{rfq_ref}'", "Check the quote.", "RFQ subject line",
                              [self.E(ref.page, "quote reference line", box, "ocr", pad=(40, 20, 40, 20))])
        f = self.f.best("local_content_decl.bid_no")
        if f and rfq_ref and f.value == rfq_ref:
            self.ok("Bid number on the local content declaration matches the RFQ", pages=[f.page])
        elif f and rfq_ref:
            self.find("medium", "inconsistency", "Bid number on the local content declaration differs from the RFQ",
                      f"'{f.value}' vs '{rfq_ref}'", "Check the declaration.", "", [self.E(f.page, source=f.source)])
        email = self.f.best("quote.addressed_to_email")
        if email:
            if email.value.lower() == self.req.submit_to_email.lower():
                self.ok("Quote is addressed to the RFQ's central submission email", self.req.submit_to_email,
                        [email.page])
            else:
                self.find("high", "inconsistency", "Quote is addressed to a different email than the RFQ's",
                          f"The quote says '{email.value}'; the RFQ says quotations go to {self.req.submit_to_email}.",
                          "Check the address on the quote.", "RFQ: submission address",
                          [self.E(email.page, "address on the quote")])
        cd = self.f.best("quote.closing_date")
        if cd and self.req.closing_date:
            if cd.value == self.req.closing_date:
                self.ok(f"Closing date on the quote ({cd.value}) matches the RFQ", pages=[cd.page])
            else:
                self.find("medium", "inconsistency", "Closing date on the quote differs from the RFQ",
                          f"Quote: {cd.value}. RFQ: {self.req.closing_date}.", "Check the quote.",
                          "RFQ: closing date", [self.E(cd.page, "closing date on the quote")])
        vd = self.f.value("quote.validity_days")
        if vd is not None and self.req.min_quote_validity_days:
            if vd >= self.req.min_quote_validity_days:
                self.ok(f"Quote validity ({vd} days) meets the minimum ({self.req.min_quote_validity_days} days)",
                        pages=self.fp("quote.validity_days"))
            else:
                self.find("high", "validity", "Quote validity shorter than required",
                          f"{vd} days vs minimum {self.req.min_quote_validity_days}.", "Extend validity.",
                          "RFQ: quotation must be firm and valid for 30 days or more",
                          [self.E(qp, "validity on the quote", self.f.best("quote.validity_days").box)])

    def spec_checks(self):
        spec = self.req.spec
        qp = self.by_no.get(self.pn("quote"))
        if spec.get("description") and qp:
            words = [re.sub(r"[^A-Z0-9]", "", w.text.upper()) for w in qp.words]
            missing = []
            for tok in re.findall(r"[A-Za-z0-9]+", spec["description"]):
                t = tok.upper()
                has_digit = any(c.isdigit() for c in t)
                hit = any(w == t or (not has_digit and len(t) >= 5 and edit_distance(w, t) <= 1) for w in words)
                if not hit:
                    missing.append(tok)
            if missing:
                self.find("medium", "spec_mismatch", "Item description on the quote does not match the specification",
                          f"The specification says '{spec['description']}'. These parts were not found on the quote: "
                          + ", ".join(missing) + ".", "Compare the item line with the specification.",
                          "Specification sheet", [self.E(qp.number, "item line"), self.E(self.pn("spec"), source="ocr")])
            else:
                self.ok("Item description matches the specification", pages=[qp.number] + self.pns("spec")[:1])
        qa, sa = self.f.value("quote.delivery_address", ""), spec.get("delivery_address", "")
        if qa and sa:
            tok = lambda s: {w for w in re.findall(r"[a-z0-9]+", s.lower())} - GENERIC_ADDRESS_WORDS
            diff = tok(qa) ^ tok(sa)
            if diff:
                self.find("medium", "spec_mismatch", "Delivery address on the quote differs from the specification",
                          f"Quote: '{qa}'. Specification: '{sa}'. Differing words: {', '.join(sorted(diff))}.",
                          "Use the address exactly as written on the specification.", "Specification sheet",
                          [self.E(self.fp('quote.delivery_address')[0], "delivery address"), self.E(self.pn("spec"))])
            else:
                self.ok("Delivery address matches the specification", pages=self.fp("quote.delivery_address"))
        brand = self.f.value("spec.brand_in_photo")
        if brand:
            self.find("medium", "spec_mismatch",
                      "The specification photo shows a branded battery, but the quote names no brand or model",
                      f"The photo on the spec sheet appears to show a battery labelled '{brand.title()}' "
                      "(read from a low-resolution image). The quote describes only '105AH deep cycle battery (674)'.",
                      "Only relevant if the buyer expects a brand match.", "Specification sheet (photo)",
                      [self.E(self.pn("spec"), "photo on the spec sheet", source="manual"),
                       self.E(self.pn("quote"), "quote item line")], tier="observation")

    # -- 8. validity dates -----------------------------------------------------
    def validity_dates(self):
        closing = iso(self.req.closing_date)
        ds = {}
        for k in ["quote", "sbd4", "sbd6_1", "sbd8", "sbd9", "local_content_decl"]:
            f = self.f.best(f"{k}.date")
            if not f or f.value is None or f.source == "ocr":
                continue
            d = iso(str(f.value))
            if d is None:
                self.find("medium", "validity", f"{DOC_LABEL[k]}: the date is not a valid date",
                          f"The date was read as '{f.value}' (page {f.page}).", "Check the date on the form.", "",
                          [self.E(f.page, "date", source=f.source)], tier=self._tier(f))
            else:
                ds[k] = (d, f)
        if closing and ds:
            late = [(k, d, f) for k, (d, f) in ds.items() if d > closing]
            for k, d, f in late:
                self.find("high", "validity", f"{DOC_LABEL[k]} is dated after the closing date",
                          f"Dated {d}; the RFQ closed {closing}.", "Check the date.", "RFQ: closing date",
                          [self.E(f.page, "date", source=f.source)], tier=self._tier(f))
            if not late:
                self.ok(f"Every dated form is dated on or before the closing date ({closing})",
                        pages=sorted({f.page for _, f in ds.values()}))
        forms_only = {k: d for k, (d, f) in ds.items() if k != "quote"}
        if forms_only and len(set(forms_only.values())) == 1:
            self.ok(f"All {len(forms_only)} forms are dated the same day", str(next(iter(forms_only.values()))),
                    sorted({ds[k][1].page for k in forms_only}))
        elif forms_only:
            self.find("medium", "inconsistency", "Forms are signed on different dates",
                      "; ".join(f"{DOC_LABEL[k]}: {d}" for k, d in forms_only.items()), "Check the dates.", "",
                      [self.E(ds[k][1].page, source=ds[k][1].source) for k in forms_only][:4],
                      tier="issue" if all(ds[k][1].confidence >= 0.8 for k in forms_only) else "observation")
        exp = iso(self.f.value("tcs.pin_expiry", ""))
        f = self.f.best("tcs.pin_expiry")
        if exp and closing:
            if exp >= closing:
                self.ok(f"SARS TCS PIN valid past the closing date (expires {exp})",
                        f"date read via {f.source}" + (" (OCR garbled this date)" if f.source != "ocr" else ""),
                        [f.page])
            else:
                self.find("high", "validity", "SARS TCS PIN expires before the closing date", str(exp),
                          "Get a new PIN.", "RFQ: TCC required", [self.E(f.page)])
        if self.f.value("tcs.purpose") == "Tender":
            self.ok("SARS TCS PIN was issued for 'Tender'", pages=self.fp("tcs.purpose"))
        elif self.f.value("tcs.purpose"):
            self.find("medium", "validity", "SARS TCS PIN purpose is not 'Tender'",
                      f"Purpose of request: {self.f.value('tcs.purpose')}.", "Check the PIN.", "RFQ: TCC required",
                      [self.E(self.f.best("tcs.purpose").page)])
        cf = self.f.best("bbbee_affidavit.commissioned")
        comm = iso(cf.value) if cf else None
        if comm and closing and comm > closing:
            self.find("high", "validity", "The B-BBEE affidavit was commissioned after the closing date",
                      f"Commissioned {comm}; closing {closing}.", "Check the date on the commissioner's stamp.",
                      "SBD 6.1 clause 1.5", [self.E(cf.page, source=cf.source)])
        elif comm and closing:
            months = (closing.year - comm.year) * 12 + closing.month - comm.month
            if 0 <= months <= 12:
                self.ok("B-BBEE affidavit is within its 12-month validity at closing",
                        f"commissioned {comm}", [cf.page])
            else:
                self.find("high", "validity", "B-BBEE affidavit is outside its 12-month validity at closing",
                          f"Commissioned {comm}; closing {closing}.", "Get a current affidavit.",
                          "SBD 6.1 clause 1.5", [self.E(cf.page, source=cf.source)])
        rd = parse_dmy(self.f.value("csd.report_date", ""))
        if rd and closing and (closing - rd).days <= 30:
            self.ok(f"CSD report is recent ({rd}, {(closing - rd).days} days before closing)",
                    pages=self.fp("csd.report_date"))

    # -- 9. declarations vs registers -----------------------------------------------
    def declarations_vs_registers(self):
        if self.f.value("sbd8.q4_1_restricted_supplier") == "No" and self.f.value("csd.restricted") == "No":
            self.ok("SBD 8 'not a restricted supplier' agrees with the CSD report",
                    pages=self.fp("sbd8.q4_1_restricted_supplier", "csd.restricted"))
        elif self.f.value("sbd8.q4_1_restricted_supplier") == "No" and self.f.value("csd.restricted") == "Yes":
            self.find("high", "inconsistency", "SBD 8 says not restricted, but the CSD report says restricted", "",
                      "Check both.", "SBD 8 clause 4.1",
                      [self.E(self.f.best("csd.restricted").page)])
        if self.f.value("sbd4.q2_7_state_employed") == "No":
            self.ok("SBD 4 'not state-employed' agrees with CSD (no director is a government employee)",
                    pages=self.fp("sbd4.q2_7_state_employed") + self.pns("csd")[1:2])
        if self.f.value("csd.vat_vendor") == "No" and self.f.value("quote.vat_registered") is False:
            self.ok("VAT status consistent: not a VAT vendor on CSD, quote and SBD 6.1",
                    pages=self.fp("quote.vat_registered", "csd.vat_vendor"))
        if self.f.value("csd.tax_status") == "Tax Compliant":
            self.ok("CSD shows tax compliant", pages=self.fp("csd.tax_status"))
        if self.f.value("csd.commodity_batteries"):
            self.ok("CSD commodity list includes batteries", pages=self.fp("csd.commodity_batteries"))
        reg = self.f.value("cipc.registration_date", "")
        yrs = self.f.value("sbd6_1.years_in_business", "")
        closing = iso(self.req.closing_date)
        m = re.search(r"\d+(?:\.\d+)?", str(yrs))
        if reg and m and closing:
            d, mo, y = (int(x) for x in reg.split("/"))
            age = (closing - date(y, mo, d)).days / 365.25
            claimed = float(m.group(0))
            f = self.f.best("sbd6_1.years_in_business")
            if claimed > age + 1:
                self.find("medium", "inconsistency", "SBD 6.1 'years in business' is more than the company's age",
                          f"SBD 6.1 says {yrs}; the CIPC certificate shows the company was registered on {reg}, "
                          f"{age:.1f} years before the closing date.", "Check the figure on SBD 6.1.",
                          "SBD 6.1 section 8.7", [self.E(f.page, "years in business", source=f.source)],
                          tier=self._tier(f) if f.source != "ocr" else "issue")
            else:
                self.ok(f"'Years in business' ({yrs}) is consistent with registration on {reg}",
                        pages=self.fp("sbd6_1.years_in_business", "cipc.registration_date"))

    # -- certificates: what is inside TCS, CSD, CIPC and the affidavit ------------------------
    def certificates(self):
        closing = iso(self.req.closing_date)
        st = self.f.best("cipc.status")
        if st:
            if re.fullmatch(r"in business", st.value.strip(), re.I):
                self.ok("CIPC: the company's status is 'In Business'", pages=[st.page])
            else:
                self.find("high", "validity", f"CIPC certificate shows the company as '{st.value}', not 'In Business'",
                          "A company that is deregistering or deregistered cannot hold a contract.",
                          "Check the CIPC status.", "CIPC registration certificate", [self.E(st.page, "Enterprise Status")])
        bs, act = self.f.best("csd.business_status"), self.f.best("csd.active")
        bad_bs = bs and not re.fullmatch(r"in business", bs.value.strip(), re.I)
        bad_act = act and act.value == "No"
        if bad_bs or bad_act:
            what = f"business status '{bs.value}'" if bad_bs else "'Is supplier active?' = No"
            self.find("high", "validity", "CSD report shows the supplier as not active", f"CSD: {what}.",
                      "Check the CSD record.", "CSD summary report", [self.E((bs or act).page, "supplier identification")])
        elif bs or act:
            self.ok("CSD: the supplier is 'In Business' and active", pages=[(bs or act).page])
        rs = self.f.best("csd.restricted")
        if rs and rs.value == "Yes":
            self.find("high", "validity", "CSD report lists the supplier as a Restricted Supplier",
                      "The CSD report says 'Restricted Supplier: Yes'.", "The bid cannot proceed as it stands.",
                      "SBD 8 clause 4.1", [self.E(rs.page, "Restricted Supplier")])
        ts = self.f.best("csd.tax_status")
        if ts and ts.value != "Tax Compliant":
            self.find("high", "validity", "CSD report does not show the supplier as tax compliant",
                      f"Overall tax status on the CSD report: '{ts.value}'.", "Get a compliant tax status.",
                      "RFQ: tax compliance", [self.E(ts.page, "Overall Tax Status")])
        rd = parse_dmy(self.f.value("csd.report_date", ""))
        if rd and closing:
            age = (closing - rd).days
            if age < 0:
                self.find("high", "validity", "The CSD report is dated after the closing date",
                          f"Report date {rd}; closing {closing}.", "Check the report date.", "RFQ: CSD report",
                          [self.E(self.f.best("csd.report_date").page, "report date")])
            elif age > 30:
                self.find("medium" if age > 90 else "low", "validity", f"The CSD report is {age} days old at closing",
                          f"Report date {rd}; closing {closing}. The RFQ asks for the latest CSD summary report.",
                          "Print a fresh report.", "RFQ: latest CSD summary report",
                          [self.E(self.f.best("csd.report_date").page, "report date")],
                          tier="issue" if age > 90 else "observation")
        ti = iso(self.f.value("tcs.issue_date", ""))
        if ti and closing and ti > closing:
            self.find("high", "validity", "The SARS tax compliance PIN was issued after the closing date",
                      f"Issued {ti}; closing {closing}.", "Check the PIN.", "RFQ: TCC required",
                      [self.E(self.f.best("tcs.issue_date").page, "issue date")])
        # the sworn affidavit's own contents
        pgs = self.pg("bbbee_affidavit")
        if pgs:
            own, lvl = self.f.value("bbbee_affidavit.black_ownership_pct"), self.f.value("bbbee_affidavit.level")
            rec, eme = self.f.value("bbbee_affidavit.recognition_pct"), self.f.best("bbbee_affidavit.eme")
            ap = pgs[0].number
            if eme is None:
                self.find("medium", "incomplete_form", "The affidavit does not say the income was under R10 million",
                          "The turnover statement that makes the business an Exempted Micro Enterprise was not found.",
                          "Check the affidavit text.", "B-BBEE EME affidavit", [self.E(ap, source="image")])
            elif eme.value is False:
                self.find("high", "validity", "The affidavit says the income exceeded R10 million",
                          "An EME affidavit is only valid below that threshold.", "Check the affidavit.",
                          "B-BBEE EME affidavit", [self.E(eme.page)])
            if own is not None and lvl is not None:
                want = 1 if own == 100 else 2 if own >= 51 else 4
                if lvl == want:
                    self.ok(f"Affidavit is internally consistent: {own}% black owned gives Level {lvl}",
                            pages=self.fp("bbbee_affidavit.level"))
                else:
                    self.find("medium", "inconsistency", "The affidavit's level does not follow from its ownership",
                              f"{own}% black owned gives an EME Level {want}, but the affidavit says Level {lvl}.",
                              "Check the affidavit.", "B-BBEE EME affidavit",
                              [self.E(self.f.best("bbbee_affidavit.level").page, "level")])
            if rec is not None and lvl in RECOGNITION and rec != RECOGNITION[lvl]:
                self.find("medium", "inconsistency", "The affidavit's recognition percentage does not match its level",
                          f"Level {lvl} is recognised at {RECOGNITION[lvl]}%, but the affidavit says {rec}%.",
                          "Check the affidavit.", "B-BBEE EME affidavit",
                          [self.E(self.f.best("bbbee_affidavit.recognition_pct").page, "recognition")])

    # -- what only a vision model can see on the page --------------------------------------
    # A vision-only "yes" to these two is never enough for an issue: asking the same model the same
    # page again in different words is not an independent check, it is the same reader with the same
    # blind spot. They need the non-AI image-analysis check (V.stray_marks: ink outside the printed
    # text/entry fields) to also find a mark on the same page before they count as more than a lead.
    _NEEDS_IMAGE_CORROBORATION = {"annotation", "overwritten"}

    def vision_inspection(self):
        sev = {"crossed_out": "medium", "overwritten": "medium", "annotation": "medium", "rotated": "low",
               "cut_off": "medium", "illegible": "low"}
        found = 0
        for key, (_q, _q2, title) in F.INSPECTIONS.items():
            for f in self.f.all(f"inspect.{key}"):
                found += 1
                page = self.by_no[f.page]
                corroborated = ""
                if key in self._NEEDS_IMAGE_CORROBORATION:
                    issue = any(r.kind in ("margin", "overwrite") for r in V.stray_marks(page, self.vocab))
                    if issue:
                        corroborated = "; a non-AI check of the page image also found ink there"
                else:
                    issue = f.confidence >= 0.8
                form = DOC_LABEL.get(page.doc_type, "page")
                checker = "A vision check" if f.source == "vision" else "A check"
                self.find(sev[key], "stray_mark", f"{title} on {form} (page {f.page})",
                          f"{checker} of the page found this ({f.source}"
                          + (", confirmed by a second look" if f.verified and f.source == "vision" else "")
                          + corroborated
                          + (f"; {f.note}" if f.note else "") + "). Alterations to a standard form should be "
                          "initialled by the bidder.", "Look at the page. If the alteration is real, ask for it to be "
                          "initialled or re-issued.", "Standard form must be returned unaltered",
                          [self.E(f.page, title, source=f.source)], tier="issue" if issue else "observation")
        for f in [x for x in self.f.items if x.field.startswith("sigmatch.")]:
            found += 1
            dtype = f.field.split(".", 1)[1]
            self.find("medium", "check_crop", f"The signature on {DOC_LABEL.get(dtype, dtype)} may be in a different hand "
                      "from the quotation's", f"A vision comparison of the two signatures judged them different"
                      + (f" ({f.note})" if f.note else "") + ". Handwriting similarity is a lead, not proof: people "
                      "sign differently on different days.", "Look at the two signatures side by side.", "",
                      [self.E(f.page, "signature", source=f.source)], tier="observation")
        log = self.f.vision_log
        if log.get("pages_inspected") and not found:
            self.ok(f"Vision check of {log['pages_inspected']} handwritten pages: nothing crossed out, overwritten, "
                    "noted in the margin, rotated or cut off")
        if log.get("failed"):
            self.res.human_only.append(
                f"{log['failed']} of {log['calls']} vision-model calls failed ({'; '.join(log['errors'])}). Entries "
                "that depend on them were not read. Check the endpoint and model (python -m tenderlens check-llm).")
        if self.f.unread:
            by = Counter(k.split(".")[0] for k, _p, _w in self.f.unread)
            self.res.human_only.append("The vision model could not read: " + ", ".join(
                f"{DOC_LABEL.get(k, k)} ({n})" for k, n in by.items()) + ". A person should read those entries.")

    def bbbee(self):
        status, exp, issue = (self.f.value("csd.bbbee_status"), self.f.value("csd.bbbee_expiry"),
                              self.f.value("csd.bbbee_issue"))
        comm = self.f.value("bbbee_affidavit.commissioned")
        if status == "Expired":
            self.find(
                "medium", "inconsistency", "The CSD report shows the B-BBEE affidavit as Expired",
                f"The CSD report lists a sworn affidavit issued {issue} that expired {exp}, status "
                f"'Expired', verification 'Manual verification required'. The pack includes a newer affidavit"
                + (f" commissioned {comm}" if comm else "") + ", but the CSD profile had not been updated when the "
                "report was run.",
                "Worth knowing if the buyer checks CSD: update the B-BBEE certificate on the CSD.",
                "SBD 6.1 clause 1.5 (proof of B-BBEE must be submitted with the bid); CSD report",
                [self.E(self.f.best("csd.bbbee_status").page, "B-BBEE section of the CSD report"),
                 self.E(self.pn("bbbee_affidavit"), "newer affidavit")], tier="observation")

    # -- 10. stray marks and initials -------------------------------------------
    def stray_marks(self):
        found = 0
        for p in self.pages:
            if p.doc_type not in ("sbd4", "sbd6_1", "sbd8", "sbd9"):
                continue
            for r in V.stray_marks(p, self.vocab):
                if r.kind not in ("margin", "overwrite"):
                    continue
                found += 1
                w, h = r.box[2] - r.box[0], r.box[3] - r.box[1]
                clear = r.kind == "overwrite" or r.ink >= 300 or max(w, h) >= 90
                where = ("in the margin, outside the printed text and every entry field" if r.kind == "margin"
                         else "over the printed text")
                self.find(
                    "medium" if clear else "low", "stray_mark",
                    f"{'Unexplained mark' if clear else 'Small mark'} on {DOC_LABEL[p.doc_type]} (page {p.number})",
                    f"A handwritten mark sits {where}. It is not a signature, date, tick or other expected entry.",
                    "Look at the crop. If it is a stray scribble, no action; if it could be read as an amendment "
                    "or annotation, consider re-signing a clean copy.",
                    "Standard form must be returned unaltered",
                    [self.E(p.number, "highlighted region", r.box, "image")],
                    tier="issue" if clear else "observation")
        if not found:
            self.ok("No stray marks found outside the entry fields on SBD 4, 6.1, 8, 9")

    def initials(self):
        have = {p.number: V.initials_present(p) for p in self.pages if p.doc_type != "blank"}
        missing = [n for n, h in have.items() if not h]
        share = 1 - len(missing) / max(1, len(have))
        if not missing:
            self.ok(f"Initials present on all {len(self.pages)} pages")
        elif share >= 0.5:  # most pages are initialled, so the bare ones are the odd ones out
            self.find("medium", "initials", f"Initials missing on {len(missing)} page(s) that the other pages carry",
                      f"Pages {missing} have no initials in the bottom-right corner, while {len(have) - len(missing)} "
                      "of the other pages do. A page without them may have been replaced or added after the pack "
                      "was initialled.", "Check these pages are the originals.", "",
                      [self.E(n, source="image") for n in missing[:4]])

    def ocr_quality(self):
        weak = [p.number for p in self.pages if p.mean_conf < 80]
        if weak:
            self.res.human_only.append(
                f"OCR confidence was below 80% on pages {weak}: anything read from those pages by OCR alone "
                "should be checked by eye.")

    def not_verifiable(self):
        if self.unmatched:
            n = sum(self.unmatched.values())
            self.res.human_only.append(
                f"{n} handwritten entries that were transcribed from pages {sorted(self.unmatched)} of the original "
                "scan could not be matched to this pack (the page is missing or its image has changed), so they were "
                "not used. Handwriting and ticks need a vision model or a person to read them on the new pages.")
        self.res.human_only += [
            "Whether the bidder was one of the suppliers actually invited (RFQ: uninvited suppliers may be "
            "disqualified). The pack does not show the invitation.",
            f"That the quote was emailed to the central address before {self.req.closing_time} on {self.req.closing_date}. "
            "That needs the sent-email timestamp, which is not in the pack.",
            "SARS TCS PIN, CSD record and the National Treasury restricted-suppliers and tender-defaulters registers "
            "would need live look-ups. The prototype reads the printed copies only.",
        ]
