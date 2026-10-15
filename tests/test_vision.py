"""The vision path, exercised with a scripted stand-in for the model.

A real Llama vision model is not available in the test environment, so `ScriptedVision` plays one: it "reads"
a page by looking up that page's image hash in the manual transcript (a perfect reader), and individual tests make
it misread, disagree with itself, or fail. That tests everything around the model: the prompts' answers being
parsed and cleaned, per-page merging, the second look on any suspicious reading, tiers (issue vs observation),
failure reporting. It does NOT test how well a given model sees: use `python -m tenderlens eval-vision` for that.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import forms as F
from tenderlens.cli import audit_pack
from tenderlens.ingest import sha1_file
from tenderlens.llm import LlamaClient

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "samples"
_TRANSCRIPT_PATH = S / "response" / "pack.transcript.json"
if not _TRANSCRIPT_PATH.is_file():
    pytest.skip(f"{_TRANSCRIPT_PATH} not present (samples are git-ignored, not shipped with the code)",
               allow_module_level=True)
TRANSCRIPT = json.loads(_TRANSCRIPT_PATH.read_text())


class ScriptedVision(LlamaClient):
    """Reads pages from the ground-truth transcript. `override[field]` replaces the first-look answer, `second[field]`
    replaces the answer to the single-question second look, `inspect[key]` is the answer to an inspection question."""

    def __init__(self, override=None, second=None, inspect=None, fail=False):
        super().__init__(base_url="http://scripted", model="scripted-vision")
        self.override, self.second, self.inspect_ans, self.fail = override or {}, second or {}, inspect or {}, fail
        self.by_sha: dict[str, dict] = {}
        for it in TRANSCRIPT["fields"]:
            self.by_sha.setdefault(it["page_sha"], {})[it["field"]] = it["value"]
        self.log: list[str] = []

    def complete_json(self, system, user, images=None):
        self.calls += 1
        if self.fail:
            self.errors.append("URLError: connection refused")
            raise OSError("connection refused")
        sha = sha1_file(Path(images[0])) if images and not Path(images[0]).name.startswith("sig-") else ""
        known = self.by_sha.get(sha, {})
        if Path(images[0]).name.startswith("sig-"):
            return {"answer": "same"}
        if "Question [" in user:  # second look at one entry
            import re
            e = next(e for es in F.FORMS.values() for e in es if e.key == re.search(r"Question \[([\w.]+)\]", user).group(1))
            self.log.append("second:" + e.key)
            if e.key in self.second:
                return {"answer": self.second[e.key]}
            v = self.override.get(e.key, known.get(e.key, "not on this page"))
            return {"answer": v}
        if "exactly yes or no" in user:  # page inspection
            return {k: self.inspect_ans.get(k, "yes" if k == "signature_seen" else "no") for k in
                    list(F.INSPECTIONS) + ["signature_seen"]}
        if user.startswith("Page "):  # the inspection second look
            for key, (_q, q2, _t) in F.INSPECTIONS.items():
                if q2 in user:
                    return {"answer": self.inspect_ans.get("2nd_" + key, "no")}
            return {"answer": "no"}
        out = {}
        for es in F.FORMS.values():
            for e in es:
                if e.key in user:
                    out[e.key] = self.override.get(e.key, known.get(e.key, "not on this page")) if (e.key in known or e.key in self.override) else "not on this page"
        return out


def run(vision, **kw):
    return audit_pack(S / "rfq" / "rfq_email.pdf", S / "rfq" / "spec_0310.pdf", S / "response" / "pack.pdf", S / "blank",
                      None, ROOT / ".cache", vision=vision, **kw)


def issues(res):
    return [f for f in res.findings if f.tier == "issue"]


def test_a_perfect_vision_reader_gives_the_clean_pack_zero_issues():
    v = ScriptedVision()
    req, res, facts, pages = run(v)
    assert [f.title for f in issues(res)] == []
    assert facts.vision_log["pages_read"] > 15 and facts.vision_log["failed"] == 0
    assert any("handwritten entries are filled in" in p.title for p in res.passed)


def test_a_yes_the_model_repeats_on_a_second_look_is_an_issue():
    v = ScriptedVision(override={"sbd4.q2_7_state_employed": "Yes"})
    _, res, facts, _ = run(v)
    assert any("question 2.7 is answered Yes" in f.title for f in issues(res))
    assert "second:sbd4.q2_7_state_employed" in v.log
    assert facts.best("sbd4.q2_7_state_employed").verified


def test_a_yes_the_second_look_disagrees_with_stays_an_observation():
    v = ScriptedVision(override={"sbd4.q2_7_state_employed": "Yes"}, second={"sbd4.q2_7_state_employed": "No"})
    _, res, _, _ = run(v)
    assert not any("question 2.7" in f.title for f in issues(res))
    assert any("question 2.7" in f.title for f in res.findings if f.tier == "observation")


def test_blank_and_both_boxes_are_cleaned_and_reported():
    v = ScriptedVision(override={"sbd8.date": "blank", "sbd4.q2_9_relationship_with_evaluator": "yes and no"})
    _, res, _, _ = run(v)
    titles = [f.title for f in issues(res)]
    assert any("SBD 8: date is blank" in t for t in titles)
    assert any("both YES and NO" in t for t in titles)


def test_a_blank_on_one_page_is_overruled_by_the_value_on_the_page_where_it_lives():
    """The model says 'blank' for fields that are not on the page it is looking at only if it is careless; each
    entry lives on one page, so a value anywhere beats a 'blank' elsewhere."""
    v = ScriptedVision()
    _, res, facts, _ = run(v)
    assert facts.best("sbd4.date").value == "2021-10-06"


def test_unreadable_entries_are_reported_not_guessed():
    v = ScriptedVision(override={"sbd4.tax_no": "unreadable"})
    _, res, facts, _ = run(v)
    assert not any("tax_no" in f.field for f in facts.items if f.source == "vision")
    assert any("could not read" in h for h in res.human_only)


def test_inspection_yes_needs_a_second_yes_to_be_an_issue():
    v = ScriptedVision(inspect={"crossed_out": "yes", "2nd_crossed_out": "yes"})
    _, res, _, _ = run(v)
    assert any("Printed text crossed out" in f.title for f in issues(res))
    v = ScriptedVision(inspect={"crossed_out": "yes", "2nd_crossed_out": "no"})
    _, res, _, _ = run(v)
    assert not any("crossed out" in f.title for f in issues(res))
    assert any("crossed out" in f.title for f in res.findings if f.tier == "observation")


def test_a_dead_endpoint_is_reported_not_silent():
    v = ScriptedVision(fail=True)
    _, res, facts, _ = run(v)
    assert facts.vision_log["failed"] > 0
    assert any("vision-model calls failed" in h for h in res.human_only)
    assert issues(res) == []  # nothing was read, so nothing is claimed


def test_transcript_outranks_a_model_reading():
    v = ScriptedVision(override={"sbd4.q2_7_state_employed": "Yes"})
    req, res, facts, _ = audit_pack(S / "rfq" / "rfq_email.pdf", S / "rfq" / "spec_0310.pdf", S / "response" / "pack.pdf",
                                    S / "blank", S / "response" / "pack.transcript.json", ROOT / ".cache", vision=v)
    assert facts.best("sbd4.q2_7_state_employed").value == "No"


@pytest.mark.parametrize("kind,raw,want", [
    (F.YESNO, "Yes", "Yes"), (F.YESNO, "ticked NO", "No"), (F.YESNO, "both", "Both"), (F.YESNO, "neither", None),
    (F.DATE, "6 October 2021", "2021-10-06"), (F.DATE, "06/10/2021", "2021-10-06"), (F.NUMBER, "R 72 324.00", 72324),
    (F.COUNT, "2", 2), (F.BOOL, "yes", True)])
def test_cleaning(kind, raw, want):
    assert F.clean(kind, raw) == want


def test_eval_vision_scores_a_perfect_reader_at_100_percent(tmp_path):
    from tenderlens.evalvision import eval_vision
    text = eval_vision(ScriptedVision(), S / "response" / "pack.pdf", S / "blank", S / "response" / "pack.transcript.json",
                       ROOT / ".cache", tmp_path)
    assert "(100%)" in text and "0 before the second look" in text
    assert (tmp_path / "eval_vision.md").exists()


def test_eval_vision_catches_a_misreading_model(tmp_path):
    from tenderlens.evalvision import eval_vision
    text = eval_vision(ScriptedVision(override={"sbd4.q2_8_business_with_state_12m": "Yes"}), S / "response" / "pack.pdf",
                       S / "blank", S / "response" / "pack.transcript.json", ROOT / ".cache", tmp_path)
    assert "Misreadings" in text and "q2_8_business_with_state_12m" in text
