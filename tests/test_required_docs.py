"""Required-documents check: a conditional document (RFQ/Tender: 'only where there is local content') must
end up present, an issue, "only a person can confirm", or "not needed" - never "not needed" just because the
RFQ/Tender doesn't happen to name a designated sector or threshold. No network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.audit import Auditor
from tenderlens.facts import Facts
from tenderlens.ingest import Page, classify_pages
from tenderlens.requirements import Requirement, Requirements, condition_negated, rules_extract

BLANK_DIR = Path(__file__).parent
COND = "only where there is local content"


def sbd62(quote="SBD forms (SBD 6.2) quote"):
    return Requirement("sbd6_2", "SBD 6.2", "e.pdf", quote, COND)


def annexure_c(quote="SBD forms (SBD 6.2 and Annexure C) quote"):
    return Requirement("annexure_c", "Annexure C Local Content Declaration: Summary Schedule", "e.pdf", quote, COND)


def test_conditional_document_present_is_a_pass():
    req = Requirements(documents=[sbd62()])
    a = Auditor(req, [Page(3, "x", doc_type="sbd6_2")], Facts(), BLANK_DIR)
    a.required_documents()
    assert any("SBD 6.2" in p.title for p in a.res.passed)
    assert not any("SBD 6.2" in f.title for f in a.res.findings) and not a.res.human_only


def test_sbd62_present_annexure_c_missing_is_an_issue_quoting_the_rfq_and_sbd62_pages():
    req = Requirements(documents=[sbd62(), annexure_c("Annexure C is required where there is local content")])
    pages = [Page(3, "x", text="SBD 6.2 Local Production and Content", doc_type="sbd6_2")]
    a = Auditor(req, pages, Facts(), BLANK_DIR)
    a.required_documents()
    found = next(f for f in a.res.findings if "Annexure C" in f.title)
    assert found.tier == "issue"
    assert "Annexure C is required where there is local content" in found.rule  # the RFQ line, quoted
    assert "SBD 6.2" in found.detail and "3" in found.detail  # names the pages where SBD 6.2 was found
    assert not any("Annexure C" in h for h in a.res.human_only)


def test_neither_sbd62_nor_annexure_c_present_needs_a_person():
    req = Requirements(documents=[sbd62(), annexure_c("Annexure C is required where there is local content")])
    a = Auditor(req, [], Facts(), BLANK_DIR)
    a.required_documents()
    assert not any("Annexure C" in f.title for f in a.res.findings)  # not a rule-decided issue
    assert not any("Annexure C" in p.title for p in a.res.passed)   # not waved through either
    human = next(h for h in a.res.human_only if "Annexure C" in h)
    assert "Confirm whether local content applies" in human
    assert "Annexure C is required where there is local content" in human


def test_not_needed_only_when_the_rfq_itself_says_the_condition_is_not_met():
    req = Requirements(documents=[annexure_c("Annexure C is required where there is local content")],
                        local_content_negated=True,
                        local_content_negated_quote="there is no local content requirement for this procurement")
    a = Auditor(req, [], Facts(), BLANK_DIR)
    a.required_documents()
    passed = next(p for p in a.res.passed if "Annexure C" in p.title)
    assert "not needed" in passed.title
    assert "no local content requirement" in passed.note
    assert not a.res.human_only and not any("Annexure C" in f.title for f in a.res.findings)


def test_absence_of_designated_sector_wording_alone_does_not_mean_not_needed():
    """The old bug: no local_content_negated/local_content_required set (the RFQ just says 'where there is
    local content', no sector/threshold, no explicit negation either) must NOT produce 'not needed' - it
    must fall through to needing a person, per test_neither_... above."""
    req = Requirements(documents=[annexure_c()])
    a = Auditor(req, [], Facts(), BLANK_DIR)
    a.required_documents()
    assert not any("not needed" in p.title for p in a.res.passed)


def test_rfq_affirming_local_content_without_a_sibling_present_is_still_an_issue():
    req = Requirements(documents=[annexure_c("Annexure C is required where there is local content")],
                        local_content_required=True, local_content_basis="the buyer's documents mention: 'designated sector'")
    a = Auditor(req, [], Facts(), BLANK_DIR)
    a.required_documents()
    found = next(f for f in a.res.findings if "Annexure C" in f.title)
    assert "designated sector" in found.detail


def test_unconditional_missing_document_is_still_an_issue():
    req = Requirements(documents=[Requirement("sbd4", "SBD 4", "e.pdf", "SBD 4 quote", "")])
    a = Auditor(req, [], Facts(), BLANK_DIR)
    a.required_documents()
    assert any("SBD 4" in f.title and f.tier == "issue" for f in a.res.findings)


# --------------------------------------------------------------------------- requirements.py: the negation detector

def test_condition_negated_spots_explicit_negation_wording():
    assert condition_negated("local content", "There is no local content requirement for this bid.")
    assert condition_negated("local content", "Local content does not apply to this procurement.")
    assert not condition_negated("local content", "SBD 6.2 and Annexure C where there is local content.")


def test_rules_extract_sets_local_content_negated_only_on_explicit_wording():
    cond = ("SBD forms (SBD 4, 8, 9, 6.1, 6.2 and Annexure C) where there is local content SBD 6.2 and "
            "Annexures will be required.")
    assert not rules_extract(cond, "e.pdf", "", "s.pdf").local_content_negated
    negated = cond + " There is no local content requirement for this procurement."
    r = rules_extract(negated, "e.pdf", "", "s.pdf")
    assert r.local_content_negated
    assert "no local content" in r.local_content_negated_quote


# --------------------------------------------------------------------------- ingest.py: Annexure C's own doc_type

def test_annexure_c_s_own_heading_is_classified_as_annexure_c():
    """Even when OCR misses the stylised 'Annex C' banner, the form's own printed subtitle - 'Local Content
    Declaration - Summary Schedule' (a dash) - is enough on its own, which is what makes this reliable at a
    normal scan resolution instead of needing a sharper re-scan just to read one heading."""
    pages = [Page(1, "x", text="Local Content Declaration - Summary Schedule\n\nNote: VAT to be excluded")]
    classify_pages(pages)
    assert pages[0].doc_type == "annexure_c"


def test_sbd62_citing_annexure_c_in_a_sentence_is_not_misread_as_annexure_c():
    """SBD 6.2 clause 1.6 names Annex C in prose, with a colon, not the form's own dash subtitle - this page
    is SBD 6.2's own continuation, not a second Annexure C page."""
    pages = [
        Page(1, "x", text="SBD 6.2\n\nDeclaration Certificate for Local Production and Content"),
        Page(2, "x", text="1.6 A bid may be disqualified if -\n\n(a) this Declaration Certificate and the "
                          "Annex C (Local Content Declaration: Summary Schedule) are not submitted as part "
                          "of the bid documentation; and\n\n(b) the bidder fails to declare that the Local "
                          "Content Declaration Templates (Annex C, D and E) have been audited."),
    ]
    classify_pages(pages)
    assert pages[1].doc_type == "sbd6_2"


# --------------------------------------------------------------------------- draft vs final pack stage

def test_draft_pack_does_not_flag_missing_initials(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "initials_present", lambda page: False)  # none of them have initials
    pages = [Page(1, "x", doc_type="sbd4"), Page(2, "x", doc_type="sbd4")]
    a = Auditor(Requirements(), pages, Facts(), BLANK_DIR, pack_stage="draft")
    a.initials()
    assert not a.res.findings and not a.res.passed  # not expected yet on a draft - no opinion either way


def test_final_pack_flags_any_missing_initial(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "initials_present", lambda page: page.number != 2)  # every page but one is initialled
    pages = [Page(1, "x", doc_type="sbd4"), Page(2, "x", doc_type="sbd4"), Page(3, "x", doc_type="sbd4")]
    a = Auditor(Requirements(), pages, Facts(), BLANK_DIR, pack_stage="final")
    a.initials()
    found = next(f for f in a.res.findings if "Initials missing" in f.title)
    assert "2" in found.detail
