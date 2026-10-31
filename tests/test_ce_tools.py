"""The CE evaluation tools: input selection (the key is never selected), matching, key parsing. No network, no dataset."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ce_eval import select_inputs
from tools.ce_score import match_defect
from tools.ce_verify_keys import page_set, parse_key


def _scenario():
    f = lambda path, role: {"path": path, "role": role}  # noqa: E731
    return {"files": [f("1CE/b tender.pdf", "tender"), f("1CE/a tender.pdf", "tender"),
                      f("1CE/T4_clean_pack.pdf", "clean_pack"), f("1CE/T4_tampered_pack.pdf", "tampered_pack"),
                      f("1CE/T4_answer_key.md", "answer_key"), f("1CE/CE1_requirements.csv", "requirements"),
                      f("1CE/.DS_Store", "ignored")]}


def test_the_runner_selects_the_tender_and_the_two_packs_and_never_the_key_or_requirements():
    got = select_inputs(_scenario(), Path("/root"))
    assert [p.name for p in got["tender"]] == ["a tender.pdf", "b tender.pdf"]
    assert got["clean"].name == "T4_clean_pack.pdf" and got["tampered"].name == "T4_tampered_pack.pdf"
    chosen = {str(p) for p in got["tender"]} | {str(got["clean"]), str(got["tampered"])}
    assert not any("answer_key" in c or "requirements" in c for c in chosen)


def test_the_runner_source_never_names_the_key_file():
    src = (Path(__file__).resolve().parents[1] / "tools" / "ce_eval.py").read_text()
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith(("#", '"""')))
    assert "parse_key" not in code and ".md" not in code and "read_text" in code  # reads only manifest.json


def test_page_lists():
    assert page_set("1-3, 7") == [1, 2, 3, 7] and page_set("p4-5") == [4, 5] and page_set("n/a") == []


def test_a_finding_on_the_right_page_with_the_right_words_is_detected():
    d = {"Test case": "T-1", "Page": "13", "Type": "date", "Expected TenderLens finding": "Attendance certificate date differs"}
    f = [{"title": "Attendance certificate date differs from the meeting", "detail": "", "evidence": [{"page": 13}]}]
    assert match_defect(d, f).result == "detected"


def test_the_same_words_on_another_page_are_a_wrong_page_and_the_right_page_alone_is_weak():
    d = {"Test case": "T-1", "Page": "13", "Type": "date", "Expected TenderLens finding": "Attendance certificate date differs"}
    other = [{"title": "Attendance certificate date differs", "detail": "", "evidence": [{"page": 99}]}]
    assert match_defect(d, other).result == "wrong page"
    unrelated = [{"title": "Page is rotated", "detail": "", "evidence": [{"page": 13}]}]
    assert match_defect(d, unrelated).result == "page only"
    assert match_defect(d, []).result == "missed"


def test_a_key_table_and_header_are_parsed(tmp_path):
    k = tmp_path / "k.md"
    k.write_text("# Tender 4 answer key\n\n- Source tender: x.pdf (T, 110 pages); body\n"
                 "- T4_clean_pack.pdf: 134 pages, 22.9 MB, SHA-256 `15da5d3895d383be...`\n\n"
                 "## 2. Requirement-by-requirement expected results\n\n"
                 "| ID | Requirement | Source | Class | Clean pack pages | Clean | Tampered | Test cases |\n|---|---|---|---|---|---|---|---|\n"
                 "| R-M1 | Meeting | p1 | mandatory | 13 | PASS | FAIL | T4-TC-01 |\n\n"
                 "## 3. Tampered pack: planted defects\n\n"
                 "| Test case | Page | Document | Requirement | Type | Exact modification | Expected TenderLens finding | Expected |\n|---|---|---|---|---|---|---|---|\n"
                 "| T4-TC-01 | 13 | Tender page 13 | R-M1 (mandatory) | date | x | y | FAIL |\n")
    key = parse_key(k)
    assert key.packs["T4_clean_pack.pdf"] == {"pages": 134, "sha_prefix": "15da5d3895d383be"}
    assert key.source_pages == 110 and list(key.requirements) == ["R-M1"] and key.defects[0]["Test case"] == "T4-TC-01"


def test_spend_is_priced_at_the_plan_rates_and_summed_over_every_saved_run(tmp_path):
    import json
    from tools.ce_eval import spend_usd, spent_so_far
    assert round(spend_usd({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}), 2) == 5.50  # 1.25 + 4.25
    for name, (pt, ct) in {"a/clean": (100_000, 10_000), "b__x/tampered": (200_000, 20_000)}.items():
        d = tmp_path / "runs" / name
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"prompt_tokens": pt, "completion_tokens": ct}))
    assert round(spent_so_far(tmp_path), 4) == round(0.3 * 1.25 + 0.03 * 4.25, 4)
    failed = tmp_path / "runs_failed" / "c__clean_1"
    failed.mkdir(parents=True)
    (failed / "run.json").write_text(json.dumps({"prompt_tokens": 100_000, "completion_tokens": 0}))
    assert round(spent_so_far(tmp_path), 4) == round(0.4 * 1.25 + 0.03 * 4.25, 4)  # failed runs still count


def test_only_grades_4_1_and_8_are_enabled_and_a_pack_that_would_pass_the_stop_is_refused(tmp_path, monkeypatch, capsys):
    import json
    import tools.ce_eval as E
    assert E.ALLOWED_GRADES == (4, 1, 8) and E.HARD_STOP_USD == 78.0
    assert E.projected_usd(1000) > 5  # a big pack projects to real money, so a late stop is possible
    monkeypatch.setattr(E, "spent_so_far", lambda out: 77.8)
    mf = {"root": str(tmp_path), "scenarios": [{"grade": 4, "folder": "4ce", "files": [
        {"path": "4ce/t.pdf", "role": "tender", "pages": 5}, {"path": "4ce/c.pdf", "role": "clean_pack", "pages": 83},
        {"path": "4ce/x.pdf", "role": "tampered_pack", "pages": 80}]}]}
    (tmp_path / "manifest.json").write_text(json.dumps(mf))
    monkeypatch.setattr(sys, "argv", ["ce_eval", "--grades", "4", "--out", str(tmp_path)])
    monkeypatch.setattr(E, "run_one", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model call attempted")))
    assert E.main() == 1
    w = json.loads((tmp_path / "budget_warnings.json").read_text())
    assert w[0]["grade"] == 4 and w[0]["spent_usd"] == 77.8 and "hard stop" in w[0]["message"]


def test_the_flag_report_labels_findings_and_removes_none():
    from tools.ce_eval import flag_report
    rep = {"findings": [{"title": "Initials missing on 29 page(s)", "tier": "issue", "evidence": [{"page": 1}]},
                        {"title": "SBD 6.1: 1 of 5 pages found", "tier": "issue", "evidence": []},
                        {"title": "Page cut off at the edge on SBD 4 (page 31)", "tier": "observation", "evidence": [{"page": 31}]},
                        {"title": "Something else", "tier": "issue", "evidence": []}]}
    out = flag_report(rep)
    assert out["total_findings"] == 4
    assert set(out["by_class"]) == {"initials", "pages_found", "page_cut_off", "other"}


def test_page_lists_written_with_and_and_missing_item_rows_are_matched():
    from tools.ce_verify_keys import page_set
    assert page_set("43 and 44") == [43, 44] and page_set("1-3, 7 & 9") == [1, 2, 3, 7, 9]
    d = {"Test case": "T-9", "Page": "n/a (absent)", "Type": "missing", "Expected TenderLens finding": "Reference letter 6 missing"}
    got = match_defect(d, [{"title": "Missing: reference letter 6", "detail": "", "evidence": []}])
    assert got.result == "detected"  # nothing to cite a page for


def test_cached_prompt_tokens_are_billed_at_the_cached_rate():
    from tools.ce_eval import spend_usd
    # 1M prompt tokens of which 400k cached, 100k completion: 0.6*1.25 + 0.4*0.15 + 0.1*4.25
    got = spend_usd({"prompt_tokens": 1_000_000, "cached_tokens": 400_000, "completion_tokens": 100_000})
    assert round(got, 4) == round(0.6 * 1.25 + 0.4 * 0.15 + 0.1 * 4.25, 4)
    assert round(spend_usd({"prompt_tokens": 1_000_000, "completion_tokens": 0}), 2) == 1.25  # older run: nothing cached
