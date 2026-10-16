"""Phase 1 of FIX_ALL_PROMPT.md: Steps 1, 2, 3A, 3C and 5. No paid calls - these exercise the Auditor's
rule methods and the report directly, with fake Facts/Requirements/Page objects, the same way test_core.py's
test_observations_are_not_issues does.
"""
from __future__ import annotations

import html as html_mod
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.audit import AuditResult, Auditor
from tenderlens.facts import Facts
from tenderlens.ingest import Page
from tenderlens.report import DISCLAIMER_LONG, DISCLAIMER_SHORT, NO_ISSUES_NOTE, render_html
from tenderlens.requirements import Requirements

BLANK_DIR = Path(__file__).parent  # no *.txt templates in here; fine, these checks don't need them


# --------------------------------------------------------------------------- Step 1: the quote's own closing date

def test_closing_date_regex_tolerates_the_ocr_underscore_artifact():
    """Tesseract sometimes reads the ruled line under 'Closing date' as a literal underscore, which the old
    \\W+ pattern (word characters excluded) could never match, so the date was silently never read at all."""
    from tenderlens.facts import ocr_facts
    p = Page(1, "x", text="Closing date_2021/10/22\nClosing time: 14:00", doc_type="quote")
    f = Facts()
    ocr_facts([p], f)
    assert f.value("quote.closing_date") == "2021-10-22"


def test_closing_date_on_the_quote_is_checked_against_the_rfq():
    f = Facts()
    f.add("quote.closing_date", "2026-01-20", 1, "ocr", 0.9)
    a = Auditor(Requirements(closing_date="2026-01-15"), [], f, BLANK_DIR)
    a.quote_checks()
    assert any("Closing date on the quote differs from the RFQ" in x.title for x in a.res.findings)


def test_closing_date_on_the_quote_matching_the_rfq_raises_nothing():
    f = Facts()
    f.add("quote.closing_date", "2026-01-20", 1, "ocr", 0.9)
    a = Auditor(Requirements(closing_date="2026-01-20"), [], f, BLANK_DIR)
    a.quote_checks()
    assert not any("Closing date" in x.title for x in a.res.findings)
    assert any("Closing date" in p.title for p in a.res.passed)


# --------------------------------------------------------------------------- Step 2: identifier reference/evidence

def test_a_handwritten_reading_never_outvotes_a_printed_one_as_the_reference():
    """Two vision (handwriting) pages independently misreading the same way must not outvote the one printed
    page, and the evidence must name both of the pages that actually differ - not the printed one."""
    f = Facts()
    f.add("id.reg_no", "2020123456/07", 1, "ocr", 0.9)
    f.add("sbd4.reg_no", "2020654321/07", 4, "vision", 0.6)
    f.add("sbd6_1.reg_no", "2020654321/07", 7, "vision", 0.6)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    found = next(x for x in a.res.findings if "Company registration number differs" in x.title)
    assert sorted(e.page for e in found.evidence) == [4, 7]
    assert all(e.source == "vision" for e in found.evidence)


def test_a_single_printed_reading_still_wins_even_against_more_handwritten_pages():
    """Same as above with a 1-vs-3 split, to be sure this isn't an accident of the 1-vs-2 case."""
    f = Facts()
    f.add("id.sa_id", "0204145509087", 25, "ocr", 0.9)
    f.add("sbd4.id_no", "0204145509078", 14, "vision", 0.6)
    f.add("sbd4.id_no", "0204145509097", 16, "vision", 0.6)
    f.add("sbd4.id_no", "0204145509012", 18, "vision", 0.6)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    found = next(x for x in a.res.findings if "SA ID number differs" in x.title)
    assert 25 not in {e.page for e in found.evidence}  # the printed page is never the odd one out


# --------------------------------------------------------------------------- Step 3A: stray notes need a witness

def test_a_vision_only_stray_note_reading_stays_an_observation(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "stray_marks", lambda page, vocab: [])  # the non-AI check finds nothing here
    f = Facts()
    f.add("inspect.annotation", True, 5, "vision", 0.9, verified=True)  # the model "confirmed" itself
    a = Auditor(Requirements(), [Page(5, "x", doc_type="sbd4")], f, BLANK_DIR)
    a.vision_inspection()
    found = next(x for x in a.res.findings if "Handwritten note outside the form fields" in x.title)
    assert found.tier == "observation"


def test_a_stray_note_backed_by_image_analysis_is_an_issue(monkeypatch):
    from tenderlens import vision as V
    region = V.Region(box=(10, 10, 50, 50), kind="margin", ink=200, note="ink outside the printed text block")
    monkeypatch.setattr(V, "stray_marks", lambda page, vocab: [region])
    f = Facts()
    f.add("inspect.annotation", True, 5, "vision", 0.9, verified=True)
    a = Auditor(Requirements(), [Page(5, "x", doc_type="sbd4")], f, BLANK_DIR)
    a.vision_inspection()
    found = next(x for x in a.res.findings if "Handwritten note outside the form fields" in x.title)
    assert found.tier == "issue"


def test_an_overwrite_reading_also_needs_the_image_witness(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "stray_marks", lambda page, vocab: [])
    f = Facts()
    f.add("inspect.overwritten", True, 5, "vision", 0.9, verified=True)
    a = Auditor(Requirements(), [Page(5, "x", doc_type="sbd4")], f, BLANK_DIR)
    a.vision_inspection()
    found = next(x for x in a.res.findings if "Entry overwritten or covered" in x.title)
    assert found.tier == "observation"


def test_crossed_out_is_unaffected_by_the_new_corroboration_rule(monkeypatch):
    """Only 'annotation' and 'overwritten' need the image witness; crossed_out etc. keep the old behaviour
    (the model's own confidence, from its self second-look) exactly as before."""
    from tenderlens import vision as V
    monkeypatch.setattr(V, "stray_marks", lambda page, vocab: [])  # would force an observation if this mattered
    f = Facts()
    f.add("inspect.crossed_out", True, 5, "vision", 0.9, verified=True)  # confidence 0.9 >= 0.8 -> issue, as before
    a = Auditor(Requirements(), [Page(5, "x", doc_type="sbd8")], f, BLANK_DIR)
    a.vision_inspection()
    found = next(x for x in a.res.findings if "Printed text crossed out" in x.title)
    assert found.tier == "issue"


# --------------------------------------------------------------------------- Step 3C: names

def test_a_handwritten_name_that_disagrees_with_a_typed_name_is_an_observation():
    f = Facts()
    f.add("cipc.director", "JOHN SMITH", 30, "ocr", 0.9)
    f.add("quote.signatory", "Someone Else", 1, "vision", 0.6)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.signatories()
    found = next(x for x in a.res.findings if "The person who signed differs" in x.title)
    assert found.tier == "observation"


def test_two_typed_names_that_disagree_is_still_an_issue():
    f = Facts()
    f.add("cipc.director", "JOHN SMITH", 30, "ocr", 0.9)
    f.add("bbbee_affidavit.deponent", "Someone Else", 25, "ocr", 0.9)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.signatories()
    found = next(x for x in a.res.findings if "The person who signed differs" in x.title)
    assert found.tier == "issue"


# --------------------------------------------------------------------------- Step 5: disclaimers

def test_disclaimers_and_the_no_issues_line_appear_in_the_report():
    out = render_html(Requirements(), AuditResult(), Facts(), [], "Bidder", "RFQ-1", "rules only")
    assert html_mod.escape(DISCLAIMER_SHORT) in out
    assert html_mod.escape(DISCLAIMER_LONG) in out
    assert NO_ISSUES_NOTE in out  # AuditResult() has no findings, so the "no issues" banner must show


# --------------------------------------------------------------------------- Step 3B: crop-and-reread

from PIL import Image

from tenderlens.ingest import Word
from tenderlens.llm import LlamaClient


class _Scripted(LlamaClient):
    """Answers each call in order, from a fixed script - no network."""

    def __init__(self, answers):
        super().__init__(base_url="http://scripted", model="scripted")
        self.answers = list(answers)

    def complete_json(self, system, user, images=None):
        self.calls += 1
        return {"answer": self.answers.pop(0)}


def _reg_no_page(tmp_path, number=4):
    """A fake page carrying the printed label 'Company Registration Number' (as it reads, word for word,
    in samples/blank/SCM-Bid_documents_SBD_4_doc.txt), so label_box can find it, with a real blank image
    behind it so the crop can actually be cut and resized."""
    img = tmp_path / f"p{number}.png"
    Image.new("RGB", (1200, 1600), "white").save(img)
    words = [Word("Company", 95.0, 50, 50, 140, 20), Word("Registration", 95.0, 200, 50, 160, 20),
             Word("Number", 95.0, 370, 50, 120, 20)]
    return Page(number, str(img), text="Company Registration Number", words=words, doc_type="sbd4")


def test_crop_reread_clears_a_misread_that_matches_the_printed_value(tmp_path):
    from tenderlens.facts import Facts, verify_identifiers
    page = _reg_no_page(tmp_path)
    f = Facts()
    f.add("id.reg_no", "2020123456/07", 1, "ocr", 0.9)
    f.add("sbd4.reg_no", "2020654321/07", 4, "vision", 0.6)
    llm = _Scripted(["2020123456/07", "2020123456/07"])  # both close-up looks now read it correctly
    verify_identifiers(llm, [page], f)
    assert f.best("sbd4.reg_no").value.replace(" ", "") == "2020123456/07"
    assert any("misread" in v.lower() for v in f.misread_variants)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    assert not any("Company registration number differs" in x.title for x in a.res.findings)


def test_crop_reread_confirms_a_real_difference_but_stays_an_observation(tmp_path):
    """A handwritten (vision) reading is a lead even when a focused crop confirms it twice - a mismatch is
    only ever an issue when every differing page is printed/typed text (OCR). The crop machinery itself is
    disabled in the real pipeline (CROP_VERIFY_ENABLED = False in facts.py); this still exercises the
    function directly, since it stays correct and testable for when it is switched back on."""
    from tenderlens.facts import Facts, verify_identifiers
    page = _reg_no_page(tmp_path)
    f = Facts()
    f.add("id.reg_no", "2020123456/07", 1, "ocr", 0.9)
    f.add("sbd4.reg_no", "2020654321/07", 4, "vision", 0.6)
    llm = _Scripted(["2020654321/07", "2020654321/07"])  # both close-up looks agree: a genuine difference
    verify_identifiers(llm, [page], f)
    fact = f.best("sbd4.reg_no")
    assert fact.value.replace(" ", "") == "2020654321/07" and fact.box is not None  # the crop is now its evidence
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    found = next(x for x in a.res.findings if "Company registration number differs" in x.title)
    assert found.tier == "observation" and 4 in {e.page for e in found.evidence}


def test_a_handwritten_identifier_mismatch_is_always_an_observation_not_an_issue():
    f = Facts()
    f.add("id.reg_no", "2020123456/07", 1, "ocr", 0.9)
    f.add("sbd4.reg_no", "2020654321/07", 4, "vision", 0.6)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    found = next(x for x in a.res.findings if "Company registration number differs" in x.title)
    assert found.tier == "observation"


def test_an_identifier_mismatch_between_two_printed_pages_is_still_an_issue():
    f = Facts()
    f.add("id.reg_no", "2020123456/07", 1, "ocr", 0.9)
    f.add("id.reg_no", "2020654321/07", 30, "ocr", 0.9)  # e.g. a different printed page, not handwriting
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    found = next(x for x in a.res.findings if "Company registration number differs" in x.title)
    assert found.tier == "issue"


def test_crop_readings_that_disagree_are_set_aside(tmp_path):
    from tenderlens.facts import Facts, verify_identifiers
    page = _reg_no_page(tmp_path)
    f = Facts()
    f.add("id.reg_no", "2020123456/07", 1, "ocr", 0.9)
    f.add("sbd4.reg_no", "2020654321/07", 4, "vision", 0.6)
    llm = _Scripted(["2020654321/07", "2020654329/07"])  # the two close-up looks disagree with each other
    verify_identifiers(llm, [page], f)
    assert f.all("sbd4.reg_no") == []  # removed - not reported as a finding either way
    assert any("disagree" in v for v in f.misread_variants)
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.identifiers()
    assert not any("Company registration number differs" in x.title for x in a.res.findings)


# --------------------------------------------------------------------------- Step 4.5: non-AI cut-off check

def test_page_cutoff_detects_a_page_much_shorter_than_its_siblings(tmp_path):
    from tenderlens import vision as V
    normal = []
    for i in range(1, 4):
        p = tmp_path / f"n{i}.png"
        Image.new("RGB", (1200, 1750), "white").save(p)
        normal.append(Page(i, str(p)))
    short_img = tmp_path / "short.png"
    Image.new("RGB", (1200, 900), "white").save(short_img)  # well under 85% of 1750
    short_page = Page(4, str(short_img))
    assert V.page_cutoff(short_page, normal + [short_page]) is True


def test_page_cutoff_detects_text_flush_against_an_edge(tmp_path):
    from tenderlens import vision as V
    img = tmp_path / "edge.png"
    Image.new("RGB", (1200, 1750), "white").save(img)
    page = Page(1, str(img), words=[Word("clipped", 95.0, 2, 800, 60, 20)])  # left=2px: against the edge
    assert V.page_cutoff(page, [page]) is True


def test_page_cutoff_is_undecided_for_an_ordinary_page(tmp_path):
    from tenderlens import vision as V
    pages = []
    for i in range(1, 4):
        p = tmp_path / f"n{i}.png"
        Image.new("RGB", (1200, 1750), "white").save(p)
        pages.append(Page(i, str(p), words=[Word("fine", 95.0, 300, 300, 60, 20)]))
    assert V.page_cutoff(pages[0], pages) is None


def test_a_decided_cutoff_page_is_not_sent_to_the_vision_model(tmp_path):
    from tenderlens.facts import Facts, inspect_pages
    normal_pages = []
    for i in range(1, 4):
        p = tmp_path / f"n{i}.png"
        Image.new("RGB", (1200, 1750), "white").save(p)
        normal_pages.append(Page(i, str(p), doc_type="sbd4"))
    short_img = tmp_path / "short.png"
    Image.new("RGB", (1200, 900), "white").save(short_img)
    short_page = Page(4, str(short_img), doc_type="sbd4")
    pages = normal_pages + [short_page]

    class CountingScript(LlamaClient):
        def __init__(self):
            super().__init__(base_url="http://scripted", model="scripted")
            self.seen_keys = []

        def complete_json(self, system, user, images=None):
            self.calls += 1
            if "exactly yes or no" in user:
                import json as _json
                keys = set(_json.loads(user.split("\n", 1)[1]).keys())
                self.seen_keys.append(keys)
                return {k: "no" for k in keys}
            return {"answer": "no"}

    llm = CountingScript()
    f = Facts()
    inspect_pages(llm, pages, f, passes=1)
    # the short page's own question set (the last one asked) must not include cut_off - the non-AI check
    # already decided it, so the vision model was never asked that question for this page
    assert "cut_off" not in llm.seen_keys[-1]
    assert any(x.field == "inspect.cut_off" and x.source == "image" and x.page == 4 for x in f.items)


def test_a_confident_whole_page_yes_on_an_uncropped_sbd4_question_is_still_only_an_observation():
    """Question 2.8 (and 2.9, 2.7.2) have no focused crop to check them - their printed number has no inline
    anchor next to their own YES/NO. A demonstrated case showed the whole-page reading of a small tick is
    not reliable even when its own whole-page second look agrees with itself, so these are capped at
    observation regardless of confidence."""
    f = Facts()
    fact = f.add("sbd4.q2_8_business_with_state_12m", "Yes", 14, "vision", 0.9, verified=True)
    assert Auditor._tier(fact) == "observation"


# --------------------------------------------------------------------------- Step 4 item 3: SBD 4 yes/no crops

def _q2_10_page(tmp_path, number=17):
    """A fake page carrying the printed number '2.10' (confirmed in samples/blank/SCM-Bid_documents_SBD_4_doc.txt,
    line 86), positioned the way the real form does: on its own, with the YES/NO choice far to its right."""
    img = tmp_path / f"q{number}.png"
    Image.new("RGB", (1200, 1600), "white").save(img)
    return Page(number, str(img), text="2.10", words=[Word("2.10", 95.0, 60, 500, 50, 22)], doc_type="sbd4")


def test_a_crop_confirming_yes_twice_stays_an_issue_with_crop_evidence(tmp_path):
    from tenderlens.facts import Facts, verify_yesno
    page = _q2_10_page(tmp_path)
    f = Facts()
    f.add("sbd4.q2_10_aware_of_other_bidder_relationship", "Yes", page.number, "vision", 0.6)
    llm = _Scripted(["Yes", "Yes"])
    verify_yesno(llm, [page], f)
    fact = f.best("sbd4.q2_10_aware_of_other_bidder_relationship")
    assert fact.value == "Yes" and fact.verified and fact.box is not None


def test_a_crop_correcting_a_whole_page_misread_clears_the_deviation(tmp_path):
    from tenderlens.facts import Facts, verify_yesno
    page = _q2_10_page(tmp_path)
    f = Facts()
    f.add("sbd4.q2_10_aware_of_other_bidder_relationship", "Yes", page.number, "vision", 0.6)
    llm = _Scripted(["No", "No"])  # the whole-page reading was wrong; the crop agrees with itself on "No"
    verify_yesno(llm, [page], f)
    fact = f.best("sbd4.q2_10_aware_of_other_bidder_relationship")
    assert fact.value == "No"
    from tenderlens import forms as F
    entry = next(e for e in F.FORMS["sbd4"] if e.key == "sbd4.q2_10_aware_of_other_bidder_relationship")
    assert not F.is_deviation(entry, fact.value)  # no longer a lead at all


def test_disagreeing_crop_reads_are_set_aside_not_reported(tmp_path):
    from tenderlens.facts import Facts, verify_yesno
    page = _q2_10_page(tmp_path)
    f = Facts()
    f.add("sbd4.q2_10_aware_of_other_bidder_relationship", "Yes", page.number, "vision", 0.6)
    llm = _Scripted(["Yes", "No"])  # the two close-up looks disagree with each other
    verify_yesno(llm, [page], f)
    assert f.best("sbd4.q2_10_aware_of_other_bidder_relationship") is None
    assert any("disagree" in v for v in f.misread_variants)


def test_crop_verify_is_switched_off_in_the_real_pipeline():
    """CROP_VERIFY_ENABLED gates both functions' only call sites (tenderlens/cli.py); off by default until
    the labels can be found a more robust way than OCR text matching on a filled-in scan."""
    from tenderlens.facts import CROP_VERIFY_ENABLED
    assert CROP_VERIFY_ENABLED is False


def test_witness_signatures_on_a_last_page_field_ignore_a_stray_reading_elsewhere():
    """The bug the prompt names: a 'last page' field (witness_signatures, declarant, signatory, date, ...)
    must not be attributed to some other page just because the model mistakenly answered there too."""
    from tenderlens import forms as F
    from tenderlens.facts import Facts, vision_extract
    sbd61 = F.FORMS["sbd6_1"]
    assert next(e for e in sbd61 if e.key == "sbd6_1.witness_signatures").where == "last"
    pages = [Page(3, "p3.png", doc_type="sbd6_1"), Page(7, "p7.png", doc_type="sbd6_1")]

    class WitnessScript(LlamaClient):
        def __init__(self):
            super().__init__(base_url="http://scripted", model="scripted")

        def complete_json(self, system, user, images=None):
            self.calls += 1
            page_no = 3 if images and "p3" in images[0] else 7
            # the model wrongly answers "0" on page 3 (no witness section there) and correctly on page 7
            return {"sbd6_1.witness_signatures": "0"} if page_no == 3 else {"sbd6_1.witness_signatures": "2"}

    f = Facts()
    vision_extract(WitnessScript(), pages, f, verify=False)
    fact = f.best("sbd6_1.witness_signatures")
    assert fact.page == 7 and fact.value == 2  # the real last page wins, not the page that merely answered first
