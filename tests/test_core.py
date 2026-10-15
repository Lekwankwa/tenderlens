"""Run with: python -m pytest tests -q   (needs samples/ present locally; they are git-ignored)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.audit import edit_distance, luhn_ok, mask, norm_name
from tenderlens.facts import plausible_sa_id
from tenderlens.llm import parse_json_loose
from tenderlens.requirements import Requirements, merge, quote_in


def test_edit_distance():
    assert edit_distance("5/2/1(0310)21/22", "5/2/4(0310)21/22") == 1


def test_mask_hides_middle():
    assert mask("0204145509087") == "020••••••••87"


def test_luhn_rejects_garbage():
    assert not luhn_ok("1234567890123")


def test_id_plausibility_rejects_sars_reference():
    assert not plausible_sa_id("2409211300300")


def test_name_normalisation():
    assert norm_name("EXCEL SUPPLY HOLDINGS PTY LTD") == norm_name("Excel Supply Holdings")


def test_llm_json_in_fences():
    assert parse_json_loose('text ```json\n{"a": 1}\n``` more') == {"a": 1}


def test_invented_requirement_is_dropped():
    src = "Quotations must be valid for 30 days or more."
    assert quote_in(src, "valid for 30 days")
    assert not quote_in(src, "must include a bank guarantee")


def test_merge_records_llm_rules_disagreement():
    a, b = Requirements(closing_date="2021-10-16"), Requirements(closing_date="2021-10-15")
    out = merge(a, b)
    assert out.closing_date == "2021-10-15"
    assert any("disagree" in n for n in out.notes)


def test_local_content_only_required_when_buyer_says_so():
    from tenderlens.requirements import rules_extract
    cond = ("SBD forms (SBD 4, 8, 9, 6.1, 6.2 and Annexure C) where there is local content SBD 6.2 and "
            "Annexures will be required.")
    assert not rules_extract(cond, "e.pdf", "", "s.pdf").local_content_required
    stated = cond + " This item falls in a designated sector with a minimum threshold of 100%."
    assert rules_extract(stated, "e.pdf", "", "s.pdf").local_content_required


def test_confusable_pairs_only():
    from tenderlens.audit import only_confusable_diff
    assert only_confusable_diff("5/2/4(0310)", "5/2/1(0310)")        # 4 and 1 are swapped by scanners
    assert not only_confusable_diff("5/2/1(0311)", "5/2/1(0310)")    # 1 vs 0 is a real change


def test_transcript_follows_page_by_hash(tmp_path):
    import json
    from tenderlens.facts import Facts, load_transcript
    from tenderlens.ingest import Page
    t = tmp_path / "t.json"
    t.write_text(json.dumps({"fields": [
        {"field": "a", "value": 1, "page": 5, "page_sha": "AAA"},
        {"field": "b", "value": 2, "page": 6, "page_sha": "BBB"}]}))
    f = Facts()
    out = load_transcript(t, f, [Page(5, "x", sha="BBB")])   # page 5 of the new pack is the old page 6
    assert f.best("b").page == 5 and f.best("a") is None
    assert out["unmatched"] == {5: 1}


def _make_text_pdf(path: Path, text: str) -> Path:
    """A minimal, hand-built single-page PDF with `text` as its only content - enough for pdftotext to read,
    without needing a PDF-writing library the project doesn't otherwise depend on."""
    stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        # wide MediaBox: this writes one long unwrapped line, and pdftotext -layout drops text past the page edge
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/MediaBox[0 0 4000 792]/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"endobj\n"
    xref_off = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref_off}\n%%EOF".encode()
    path.write_bytes(bytes(out))
    return path


def test_rfq_with_several_documents_reads_requirements_from_any_of_them(tmp_path):
    """The buyer split their RFQ across two files; the closing date is only in the second one."""
    from tenderlens.requirements import build_requirements

    cover = _make_text_pdf(tmp_path / "cover.pdf", "Covering letter. See the attached annexure for dates.")
    annexure = _make_text_pdf(
        tmp_path / "annexure.pdf",
        "Please note that the closing date and time for submission of this quotation is on or before the "
        "15 December 2026 AT 11h00, submitted only to this central email address: tenders@buyer.gov.za")

    req = build_requirements([cover, annexure], "", "", None)
    # the date is only in annexure.pdf; finding it proves both files' text were read and searched
    assert req.closing_date == "2026-12-15" and req.closing_time == "11:00"
    assert req.submit_to_email == "tenders@buyer.gov.za"

    # a single file still works exactly as before (not wrapped in a list)
    single = build_requirements(annexure, "", "", None)
    assert single.closing_date == "2026-12-15"


def test_observations_are_not_issues():
    from tenderlens.audit import Auditor
    from tenderlens.facts import Facts
    from tenderlens.requirements import Requirements
    a = Auditor(Requirements(), [], Facts(), Path(__file__).parent)
    a.find("low", "x", "small", "d", "a")
    a.find("medium", "x", "real", "d", "a")
    a.find("medium", "x", "context", "d", "a", tier="observation")
    assert [f.tier for f in a.res.findings] == ["observation", "issue", "observation"]
