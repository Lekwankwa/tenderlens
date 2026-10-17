"""Step 1 of the failed-vision-handling fix: the "Only a person can confirm" wording must say vision was
connected but busy when it was configured and attempted but failed, and only say "no vision model is
connected" when that's actually true. No network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.audit import AuditResult, Auditor
from tenderlens.facts import Facts
from tenderlens.llm import LlamaClient
from tenderlens.report import render_html
from tenderlens.requirements import Requirements

BLANK_DIR = Path(__file__).parent


class _AlwaysFailsVision(LlamaClient):
    """Configured (so audit_pack treats this as a real Complete check), but every call fails - standing in
    for a vision endpoint that's persistently busy even after _post()'s own retries are exhausted."""

    def __init__(self):
        super().__init__(base_url="http://vision.invalid", model="scripted-vision")

    def complete_json(self, system, user, images=None):
        self.calls += 1
        self.errors.append("HTTP 429 from http://vision.invalid (scripted-vision): Model busy, retry later")
        raise RuntimeError("scripted: vision endpoint always busy")


def test_says_not_connected_when_vision_was_never_configured():
    f = Facts()  # vision_log["model"] stays "" - never set, the way cli.py/app.py leave it when unconfigured
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.handwritten_entries()
    msg = next(h for h in a.res.human_only if "Handwritten entries were NOT read" in h)
    assert "no vision model is connected" in msg
    assert "OLLAMA.md" in msg


def test_says_busy_not_disconnected_when_vision_was_configured_but_every_call_failed():
    f = Facts()
    f.vision_log["model"] = "meta-llama/Llama-4-Scout-17B-16E-Instruct"
    f.vision_log["calls"] = 3
    f.vision_log["failed"] = 3
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.handwritten_entries()
    msg = next(h for h in a.res.human_only if "Handwriting, ticks and stamps were NOT read" in h)
    assert "was connected" in msg and "busy" in msg
    assert "no vision model is connected" not in msg and "OLLAMA.md" not in msg
    assert "run the check again" in msg.lower()


def test_says_busy_with_the_precheck_s_own_reason_when_it_stopped_before_any_call():
    f = Facts()
    f.vision_log["model"] = "meta-llama/Llama-4-Scout-17B-16E-Instruct"
    f.vision_log["stopped_early"] = "LLMError: HTTP 429 from https://api.deepinfra.com/v1/openai: Model busy"
    a = Auditor(Requirements(), [], f, BLANK_DIR)
    a.handwritten_entries()
    msg = next(h for h in a.res.human_only if "Handwriting, ticks and stamps were NOT read" in h)
    assert "429" in msg or "Model busy" in msg


# --------------------------------------------------------------------------- step 3: complete vs incomplete

def _synthetic_pack(path, n=1):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=400, height=500)
    with open(path, "wb") as f:
        w.write(f)
    return path


def test_a_complete_check_where_every_vision_call_succeeds_is_marked_complete(tmp_path):
    from tenderlens.cli import audit_pack
    rfq = _synthetic_pack(tmp_path / "rfq.pdf", 1)
    pack = _synthetic_pack(tmp_path / "pack.pdf", 1)
    req, res, facts, pages = audit_pack(rfq, None, pack, BLANK_DIR, None, tmp_path / "work",
                                        llm=LlamaClient(base_url="", model=""),
                                        vision=LlamaClient(base_url="", model=""))
    assert facts.vision_complete  # vision wasn't even configured, so nothing could fail - trivially complete


def test_a_complete_check_where_vision_never_answers_is_marked_incomplete(tmp_path):
    from tenderlens.cli import audit_pack
    rfq = _synthetic_pack(tmp_path / "rfq.pdf", 1)
    pack = _synthetic_pack(tmp_path / "pack.pdf", 1)
    req, res, facts, pages = audit_pack(rfq, None, pack, BLANK_DIR, None, tmp_path / "work",
                                        llm=LlamaClient(base_url="", model=""), vision=_AlwaysFailsVision())
    assert facts.vision_complete is False
    assert facts.vision_log["stopped_early"]  # the precheck call is what failed here


def test_report_shows_the_incomplete_banner_only_for_an_incomplete_complete_check():
    facts = Facts()
    facts.vision_complete = False
    facts.vision_log["failed_pages"] = {4, 7}
    html = render_html(Requirements(), AuditResult(), facts, [], "Bidder", "REF-1", "rules only",
                       check_mode="complete")
    assert "did not use one of your checks" in html
    assert "4" in html and "7" in html


class _WorksThisTime(LlamaClient):
    """A vision client that succeeds, for the 'Run again' half of the test: the first audit_pack call used
    _AlwaysFailsVision, this one simulates the provider being fine on the retry."""

    def __init__(self):
        super().__init__(base_url="http://vision.ok", model="scripted-vision")

    def complete_json(self, system, user, images=None):
        self.calls += 1
        return {"ok": True}


def test_rerun_vision_recovers_an_incomplete_check_without_rereading_the_rfq(tmp_path):
    from tenderlens.cli import audit_pack, rerun_vision
    rfq = _synthetic_pack(tmp_path / "rfq.pdf", 1)
    pack = _synthetic_pack(tmp_path / "pack.pdf", 1)
    req, res, facts, pages = audit_pack(rfq, None, pack, BLANK_DIR, None, tmp_path / "work",
                                        llm=LlamaClient(base_url="", model=""), vision=_AlwaysFailsVision())
    assert facts.vision_complete is False  # the setup: first attempt was incomplete

    res2, facts2 = rerun_vision(req, pages, BLANK_DIR, vision=_WorksThisTime())
    assert facts2.vision_complete is True
    assert "render" not in facts2.stage_times and "text_model" not in facts2.stage_times  # nothing re-read


def test_report_omits_the_incomplete_banner_for_a_fast_check_regardless():
    facts = Facts()
    facts.vision_complete = False  # irrelevant in fast mode - vision was never supposed to run
    html = render_html(Requirements(), AuditResult(), facts, [], "Bidder", "REF-1", "rules only",
                       check_mode="fast")
    assert "did not use one of your checks" not in html
