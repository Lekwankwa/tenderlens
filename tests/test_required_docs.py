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


def test_initials_finding_cites_every_missing_page(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "initials_present", lambda page: page.number in (1, 2, 4, 9))  # initialled pack, 6 pages dropped
    pages = [Page(n, "x", doc_type="sbd4") for n in range(1, 10)]
    a = Auditor(Requirements(), pages, Facts(), BLANK_DIR, pack_stage="final")
    a.initials()
    found = next(f for f in a.res.findings if "Initials missing" in f.title)
    assert [e.page for e in found.evidence] == [3, 5, 6, 7, 8]


def _inspection_auditor(**leads):
    """An Auditor over three SBD 4 pages with these vision leads: {field: (confidence, verified)} on page 1."""
    pages = [Page(n, "x", doc_type="sbd4") for n in (1, 2, 3)]
    facts = Facts()
    for field, (conf, verified) in leads.items():
        facts.add(field, True, 1, "vision", conf, verified=verified)
    return Auditor(Requirements(), pages, facts, BLANK_DIR)


def test_a_lead_the_second_look_did_not_confirm_is_still_listed_as_an_observation():
    a = _inspection_auditor(**{"inspect.cut_off": (0.4, False)})
    a.vision_inspection()
    found = next(f for f in a.res.findings if "cut off" in f.title.lower())
    assert found.tier == "observation"


def test_a_confirmed_cut_off_page_is_described_as_a_scan_problem_not_an_amendment():
    a = _inspection_auditor(**{"inspect.cut_off": (0.9, True)})
    a.vision_inspection()
    found = next(f for f in a.res.findings if "cut off" in f.title.lower())
    assert "initialled" not in found.detail and "missing from the scan" in found.detail
    assert "initialled" not in found.action


def test_a_blank_identifier_reading_does_not_crash_the_identifier_comparison():
    facts = Facts()
    facts.add("sbd4.id_no", None, 16, "vision", 0.6)       # read as blank
    facts.add("id.sa_id", "8001015009087", 1, "ocr", 0.9)
    a = Auditor(Requirements(), [Page(1, "x", doc_type="quote"), Page(16, "x", doc_type="sbd4")], facts, BLANK_DIR)
    a.identifiers()  # must not raise


def test_a_pack_with_no_initials_on_its_first_five_pages_is_not_flagged_page_by_page(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "initials_present", lambda page: False)
    pages = [Page(n, "x", doc_type="sbd4") for n in range(1, 9)]
    a = Auditor(Requirements(), pages, Facts(), BLANK_DIR, pack_stage="final")
    a.initials()
    assert not a.res.findings
    assert any("not checked" in line and "first five pages" in line for line in a.res.human_only)


def test_blank_pages_are_not_part_of_the_first_five_and_a_pack_initialled_there_is_still_checked(monkeypatch):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "initials_present", lambda page: page.number in (2, 3))  # initialled on two of the first five
    pages = [Page(1, "x", doc_type="blank")] + [Page(n, "x", doc_type="sbd4") for n in range(2, 10)]
    a = Auditor(Requirements(), pages, Facts(), BLANK_DIR, pack_stage="final")
    a.initials()
    found = next(f for f in a.res.findings if "Initials missing" in f.title)
    assert [e.page for e in found.evidence][:3] == [4, 5, 6]


def test_the_tender_designates_sbd_forms_only_when_its_own_text_names_them():
    from tenderlens.ingest import tender_designates_sbd
    sbd = "Bidders must return the SBD 4 and SBD 6.1 forms with the quotation. " * 6
    nec = "The contractor shall complete the works under NEC3 Engineering and Construction Contract Option B. " * 6
    mbd = "Bidders must return MBD 4, MBD 6.1 and MBD 9 with their tender documents. " * 6
    assert tender_designates_sbd(sbd) is True and tender_designates_sbd(nec) is False
    assert tender_designates_sbd(mbd) is True  # the municipal name for the same forms
    assert tender_designates_sbd("") is None and tender_designates_sbd(None) is None  # no text layer: ask the pack


def test_sbd_pages_in_a_tender_that_does_not_name_sbd_forms_are_left_unchecked(tmp_path, monkeypatch):
    from tenderlens import cli
    pages = [Page(1, "x", doc_type="sbd4"), Page(2, "x", doc_type="sbd9"), Page(3, "x", doc_type="csd")]
    monkeypatch.setattr(cli, "_tender_text", lambda rfq, spec: "NEC3 contract. " * 40)
    note = cli.apply_sbd_gate(pages, [], None)
    assert [p.doc_type for p in pages] == ["unknown", "unknown", "csd"] and "SBD form checks were not run" in note
    pages = [Page(1, "x", doc_type="sbd4")]
    monkeypatch.setattr(cli, "_tender_text", lambda rfq, spec: "Return SBD 4 with your bid. " * 20)
    assert cli.apply_sbd_gate(pages, [], None) == "" and pages[0].doc_type == "sbd4"


def test_a_tender_with_no_text_is_judged_by_the_pack_itself(monkeypatch):
    from tenderlens import cli
    monkeypatch.setattr(cli, "_tender_text", lambda rfq, spec: None)
    headed = [Page(n, "x", text="SBD 4 DECLARATION OF INTEREST", doc_type="sbd4") for n in (1, 2)]
    assert cli.apply_sbd_gate(headed, [], None) == "" and headed[0].doc_type == "sbd4"
    bare = [Page(n, "x", text="nothing here", doc_type="sbd4") for n in (1, 2)]  # type only inherited, no header of their own
    assert "SBD form checks were not run" in cli.apply_sbd_gate(bare, [], None)


def test_pages_without_a_header_stop_inheriting_the_form_before_them():
    from tenderlens.ingest import classify_pages
    pages = [Page(1, "x", text="SBD 9 INDEPENDENT BID DETERMINATION " + "text " * 10)] + \
            [Page(n, "x", text="general conditions of contract " * 5) for n in range(2, 12)]
    classify_pages(pages)
    assert pages[0].doc_type == "sbd9"
    assert [p.doc_type for p in pages[1:]].count("sbd9") <= 4 and pages[-1].doc_type == "unknown"


def _doc_auditor(pages, docs):
    from tenderlens.requirements import Requirement
    return Auditor(Requirements(documents=[Requirement(i, l, "t.pdf", q) for i, l, q in docs]), pages, Facts(), BLANK_DIR)


def test_a_required_document_shown_by_its_wording_is_not_reported_missing():
    pages = [Page(1, "x", text="Reference letter from Matlosana Local Municipality confirming the completed works " * 3),
             Page(2, "x", text="MBD 4 DECLARATION OF INTEREST " + "bidder particulars " * 20)]
    a = _doc_auditor(pages, [("ref", "Two completion certificates or reference letters", "reference letters"),
                             ("mbd4", "Declaration of Interest form (MBD4)", "MBD4"),
                             ("cidb", "Proof of CIDB grading", "CIDB")])
    a.required_documents()
    titles = [f.title for f in a.res.findings]
    assert "Missing: Proof of CIDB grading" in titles                       # nothing in the pack shows it
    assert "Missing: Declaration of Interest form (MBD4)" not in titles      # MBD 4 is on page 2
    assert "Missing: Two completion certificates or reference letters" not in titles
    assert any("Required document present" in p.title for p in a.res.passed)


def test_a_quotation_is_found_by_wording_when_the_classifier_does_not_know_the_form():
    pages = [Page(1, "x", text="FORM OF OFFER AND ACCEPTANCE " + "the tenderer offers to do the works " * 10)]
    a = _doc_auditor(pages, [])
    a.required_documents()
    assert not [f for f in a.res.findings if "quotation" in f.title.lower()]


def test_municipal_mbd_headers_classify_like_the_sa_sbd_ones():
    from tenderlens.ingest import classify_pages
    pages = [Page(1, "x", text="MBD 4 DECLARATION OF INTEREST " + "text " * 20),
             Page(2, "x", text="MBD 9 CERTIFICATE OF INDEPENDENT BID DETERMINATION " + "text " * 20),
             Page(3, "x", text="MBD 6.1 PREFERENCE POINTS CLAIM FORM " + "text " * 20)]
    classify_pages(pages)
    assert [p.doc_type for p in pages] == ["sbd4", "sbd9", "sbd6_1"]


def test_unlabelled_pages_after_an_mbd_form_do_not_become_that_form():
    from tenderlens.ingest import classify_pages
    pages = [Page(1, "x", text="MBD 9 CERTIFICATE OF INDEPENDENT BID DETERMINATION " + "text " * 20)] + \
            [Page(n, "x", text="schedule of quantities and rates for earthworks " * 6) for n in range(2, 14)]
    classify_pages(pages)
    assert sum(p.doc_type == "sbd9" for p in pages) <= 5 and pages[-1].doc_type == "unknown"


def test_two_scans_of_the_same_sheet_are_flagged_but_two_different_pages_are_not(tmp_path):
    import numpy as np
    from PIL import Image
    from tenderlens.ingest import near_duplicate_pairs
    rng = np.random.default_rng(3)
    base = (rng.random((256, 192)) > 0.93).astype(np.uint8) * 255         # a page of "ink"
    def save(name, arr):
        p = tmp_path / name
        Image.fromarray(255 - arr).convert("L").save(p)
        return str(p)
    noise = lambda a: np.clip(a.astype(int) + rng.integers(-12, 12, a.shape), 0, 255).astype(np.uint8)  # a re-scan
    other = (rng.random((256, 192)) > 0.93).astype(np.uint8) * 255
    text = "audited annual financial statements for the year ended " * 5
    pages = [Page(1, save("a.png", base), text=text), Page(2, save("b.png", noise(base)), text=text),
             Page(3, save("c.png", other), text=text)]
    pairs = near_duplicate_pairs(pages)
    assert [(a, b) for a, b, *_ in pairs] == [(1, 2)]
    distinct = [Page(1, pages[0].image_path, text=text), Page(2, pages[2].image_path, text="a different page " * 30)]
    assert near_duplicate_pairs(distinct) == []


def _mark_auditor(monkeypatch, regions, words=()):
    from tenderlens import vision as V
    monkeypatch.setattr(V, "stray_marks", lambda page, vocab: regions)
    monkeypatch.setattr(V, "signature_zone", lambda page, dtype: ((100, 500, 600, 700), False))
    p = Page(5, "x", doc_type="sbd8")
    p.words = list(words)
    return Auditor(Requirements(), [p], Facts(), BLANK_DIR)


def test_a_small_mark_away_from_signatures_and_amounts_is_counted_not_listed(monkeypatch):
    from tenderlens import vision as V
    a = _mark_auditor(monkeypatch, [V.Region(box=(900, 100, 930, 130), kind="margin", ink=80, note="")])
    a.stray_marks()
    assert not a.res.findings and any("1 small isolated mark(s)" in h for h in a.res.human_only)


def test_a_small_mark_on_the_signature_zone_is_still_reported(monkeypatch):
    from tenderlens import vision as V
    a = _mark_auditor(monkeypatch, [V.Region(box=(200, 550, 230, 580), kind="margin", ink=80, note="")])
    a.stray_marks()
    assert [f.title for f in a.res.findings] == ["Small mark on SBD 8 (page 5)"]


def test_a_small_mark_beside_an_amount_is_still_reported(monkeypatch):
    from tenderlens import vision as V
    from tenderlens.ingest import Word
    amount = Word("R72,324.00", 90, 700, 300, 800, 320)
    a = _mark_auditor(monkeypatch, [V.Region(box=(820, 305, 850, 330), kind="margin", ink=80, note="")], [amount])
    a.stray_marks()
    assert len(a.res.findings) == 1


def test_a_large_mark_is_reported_wherever_it_is(monkeypatch):
    from tenderlens import vision as V
    a = _mark_auditor(monkeypatch, [V.Region(box=(900, 100, 1100, 200), kind="margin", ink=1100, note="")])
    a.stray_marks()
    assert a.res.findings[0].title.startswith("Unexplained mark on SBD 8")


def _footer_pack(tmp_path, n=20, footer_on=4, landscape=()):
    """n pages; `footer_on` of them have confident text touching the bottom edge (a printed footer the OCR reads on only some
    pages); pages in `landscape` are scanned sideways."""
    from PIL import Image
    from tenderlens.ingest import Word
    pages = []
    for i in range(1, n + 1):
        size = (1754, 1240) if i in landscape else (1240, 1754)
        img = tmp_path / f"f{i}.png"
        Image.new("RGB", size, "white").save(img)
        p = Page(i, str(img), text="x " * 100, doc_type="unknown")
        if i <= footer_on:
            p.words = [Word("footer", 90, 100, size[1] - 12, 80, 10)]
        pages.append(p)
    return pages


def test_a_footer_printed_on_the_pack_is_not_a_cut_off_page(tmp_path):
    from tenderlens import vision as V
    pages = _footer_pack(tmp_path, n=20, footer_on=4)       # 20% of pages touch the bottom edge: running footer
    assert V.page_cutoff(pages[0], pages) is None


def test_text_clipped_on_one_page_of_a_pack_is_still_a_cut_off_page(tmp_path):
    from tenderlens import vision as V
    pages = _footer_pack(tmp_path, n=30, footer_on=1)       # 1 page in 30: not furniture
    assert V.page_cutoff(pages[0], pages) is True


def test_a_page_scanned_sideways_is_not_short(tmp_path):
    from tenderlens import vision as V
    pages = _footer_pack(tmp_path, n=12, footer_on=0, landscape=(5,))
    assert V.page_cutoff(pages[4], pages) is None
