"""Step 4 of the failed-vision-handling fix: one JSONL record per check, with no document contents or
personal data - just counts and booleans. No network, no paid calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import check_log


def test_record_writes_one_json_line_with_the_expected_fields(tmp_path):
    path = tmp_path / "check_log.jsonl"
    check_log.record(check_mode="complete", complete=False, pages_failed=[7, 4], vision_calls=12,
                     vision_retries=3, text_retries=0, credit_used=False, path=path)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["check_mode"] == "complete"
    assert row["complete"] is False
    assert row["pages_failed"] == [4, 7]  # sorted
    assert row["vision_calls"] == 12 and row["vision_retries"] == 3
    assert row["credit_used"] is False
    assert row["free_rerun"] is False  # default


def test_record_never_includes_document_content_or_identifying_fields(tmp_path):
    path = tmp_path / "check_log.jsonl"
    check_log.record(check_mode="fast", complete=True, pages_failed=[], vision_calls=0, vision_retries=0,
                     text_retries=1, credit_used=True, path=path)
    row = json.loads(path.read_text().strip())
    forbidden = {"bidder", "rfq_reference", "filename", "pack", "text", "name", "code", "email", "file_sha256"}
    assert not (set(row) & forbidden)


def test_record_appends_rather_than_overwriting(tmp_path):
    path = tmp_path / "check_log.jsonl"
    for i in range(3):
        check_log.record(check_mode="complete", complete=True, pages_failed=[], vision_calls=i,
                         vision_retries=0, text_retries=0, credit_used=True, path=path)
    assert len(path.read_text().strip().splitlines()) == 3


def test_record_can_carry_total_and_per_stage_seconds(tmp_path, capsys):
    path = tmp_path / "check_log.jsonl"
    check_log.record(check_mode="complete", complete=True, pages_failed=[], vision_calls=5, vision_retries=0,
                     text_retries=0, credit_used=False, seconds=41.234, stages={"ocr": 3.04, "vision_first": 12.96},
                     path=path)
    row = json.loads(path.read_text().strip())
    assert row["seconds"] == 41.2 and row["stages"] == {"ocr": 3.0, "vision_first": 13.0}
    assert capsys.readouterr().out.startswith("check_log {")  # also on stdout, which Cloud Run keeps


def test_record_can_carry_token_counts(tmp_path):
    path = tmp_path / "check_log.jsonl"
    check_log.record(check_mode="complete", complete=True, pages_failed=[], vision_calls=2, vision_retries=0,
                     text_retries=0, credit_used=False, tokens={"vision_prompt": 5800, "vision_completion": 9100},
                     path=path)
    assert json.loads(path.read_text().strip())["tokens"] == {"vision_prompt": 5800, "vision_completion": 9100}
