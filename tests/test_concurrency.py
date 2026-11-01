"""Parallel OCR and vision calls: no network, no paid calls.

Covers what the performance prompt asked for: parallel and sequential give identical findings and order;
a 429 is retried and then succeeds (already in test_llm.py; the isolation/token-total side is here); a
permanently failing call doesn't stop the rest of a batch; token totals and call counts stay correct under
concurrency; the timing fields exist.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_llm import Mock, ok  # the local mock OpenAI-compatible HTTP server, reused from test_llm.py

from tenderlens.facts import Facts, inspect_pages, vision_extract
from tenderlens.ingest import Page, Word
from tenderlens.llm import ModelClient, run_concurrent


def client(m, **kw):
    return ModelClient(base_url=m.url, model="scripted", api_key="sk-test", **kw)


# --------------------------------------------------------------------------- run_concurrent itself

def test_run_concurrent_preserves_task_order_regardless_of_finish_order():
    """Tasks that finish in a different order than they were submitted (the slow one first) must still come
    back in the SAME order as the `tasks` list - concurrent.futures.Executor.map()'s own guarantee."""
    order_finished = []
    lock = threading.Lock()

    def make(n, delay):
        def task():
            time.sleep(delay)
            with lock:
                order_finished.append(n)
            return n
        return task

    tasks = [make(1, 0.06), make(2, 0.0), make(3, 0.03)]  # task 1 finishes LAST despite being submitted first
    results = run_concurrent(tasks, max_workers=3)
    assert results == [1, 2, 3]  # submission order, not completion order
    assert order_finished != [1, 2, 3]  # sanity: completion order really was different


def test_run_concurrent_on_done_tick_is_thread_safe_and_fires_once_per_task():
    hits = []
    lock = threading.Lock()

    def on_done():
        with lock:
            hits.append(1)

    tasks = [(lambda i=i: i) for i in range(20)]
    run_concurrent(tasks, max_workers=8, on_done=on_done)
    assert len(hits) == 20  # no lost or duplicated ticks under real thread contention


def test_token_totals_and_call_count_stay_correct_under_concurrency():
    m = Mock([ok({"a": 1}, prompt_tokens=100, completion_tokens=20)])  # same answer for every request
    c = client(m)
    tasks = [(lambda: c.complete_json("sys", "u")) for _ in range(10)]
    run_concurrent(tasks, max_workers=5)
    assert c.calls == 10
    assert c.prompt_tokens == 1000 and c.completion_tokens == 200  # 10 * (100, 20), no lost increments
    m.close()


def test_a_permanently_failing_call_is_isolated_not_fatal_to_the_batch(monkeypatch):
    monkeypatch.setattr("tenderlens.llm.time.sleep", lambda s: None)
    m = Mock([(401, {"error": "bad key"})])  # every call fails the same, non-retryable way
    c = client(m)

    def one(i):
        try:
            return ("ok", c.complete_json("sys", f"u{i}"))
        except Exception as err:  # noqa: BLE001
            return ("failed", str(err))

    tasks = [(lambda i=i: one(i)) for i in range(5)]
    results = run_concurrent(tasks, max_workers=5)
    assert all(status == "failed" for status, _ in results)  # every call failed...
    assert len(results) == 5  # ...but the batch itself completed, nothing raised out of run_concurrent
    assert c.errors and len(c.errors) == 5  # each failure recorded, not silently dropped
    m.close()


# --------------------------------------------------------------------------- vision_extract: parallel == sequential

class _Scripted(ModelClient):
    """A deterministic 'model' keyed by the page image's own path - the same page always answers the same
    way regardless of which thread asks or in what order, so a parallel and a sequential run must produce
    identical facts."""

    def __init__(self, by_page: dict, fail_for: set | None = None):
        super().__init__(base_url="http://scripted", model="scripted")
        self.by_page, self.fail_for = by_page, fail_for or set()

    def complete_json(self, system, user, images=None):
        self.calls += 1
        path = images[0] if images else ""
        if path in self.fail_for:
            self.errors.append(f"scripted failure for {path}")
            raise RuntimeError("scripted failure")
        return self.by_page.get(path, {})


def _blank_pages(tmp_path, n, doc_type="sbd4"):
    pages = []
    for i in range(1, n + 1):
        img = tmp_path / f"p{i}.png"
        Image.new("RGB", (100, 100), "white").save(img)
        pages.append(Page(i, str(img), doc_type=doc_type))
    return pages


def test_vision_extract_gives_identical_facts_sequential_vs_parallel(tmp_path, monkeypatch):
    pages = _blank_pages(tmp_path, 5)
    # every page answers the same handwritten fields the same way
    answers = {p.image_path: {"sbd4.name_field": "Excel Supply Holdings", "sbd4.id_no": "0204145509087",
                              "sbd4.q2_7_state_employed": "No"} for p in pages}

    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "1")
    f_seq = Facts()
    vision_extract(_Scripted(answers), pages, f_seq, verify=True)

    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "8")
    f_par = Facts()
    vision_extract(_Scripted(answers), pages, f_par, verify=True)

    def normalise(facts):
        return sorted((f.field, f.value, f.page, f.source) for f in facts.items)

    assert normalise(f_seq) == normalise(f_par)


def test_inspect_pages_gives_identical_facts_sequential_vs_parallel(tmp_path, monkeypatch):
    pages = _blank_pages(tmp_path, 4, doc_type="sbd8")
    answers = {p.image_path: {"crossed_out": "yes", "overwritten": "no", "annotation": "no", "rotated": "no",
                              "cut_off": "no", "illegible": "no"} for p in pages}

    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "1")
    f_seq = Facts()
    inspect_pages(_Scripted(answers), pages, f_seq, passes=1)

    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "8")
    f_par = Facts()
    inspect_pages(_Scripted(answers), pages, f_par, passes=1)

    def normalise(facts):
        return sorted((f.field, f.value, f.page, f.source) for f in facts.items)

    assert normalise(f_seq) == normalise(f_par)
    assert len(f_seq.items) > 0  # sanity: the scripted "yes" on crossed_out really did produce a finding


def test_a_failing_page_does_not_stop_vision_extract_reading_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "4")
    pages = _blank_pages(tmp_path, 4)
    answers = {p.image_path: {"sbd4.name_field": "Excel Supply Holdings"} for p in pages}
    bad_page = pages[2].image_path
    llm = _Scripted(answers, fail_for={bad_page})
    facts = Facts()
    vision_extract(llm, pages, facts, verify=False)
    # the 3 good pages still produced a reading; the bad one didn't crash the run
    name_fact = facts.best("sbd4.name_field")
    assert name_fact is not None and name_fact.value == "Excel Supply Holdings"
    assert llm.errors  # the failure on page 3 was recorded, not silently swallowed
    assert pages[2].number in facts.vision_log["failed_pages"]  # page 3 is page number 3, not just "a" page


# --------------------------------------------------------------------------- timing

def test_stage_times_are_recorded(tmp_path):
    from pypdf import PdfWriter

    from tenderlens.cli import audit_pack

    def make_pdf(path, n=1):
        w = PdfWriter()
        for _ in range(n):
            w.add_blank_page(width=400, height=500)
        with open(path, "wb") as f:
            w.write(f)
        return path

    rfq = make_pdf(tmp_path / "rfq.pdf")
    pack = make_pdf(tmp_path / "pack.pdf")
    req, res, facts, pages = audit_pack(rfq, None, pack, tmp_path, None, tmp_path / "work",
                                        llm=ModelClient(base_url="", model=""),
                                        vision=ModelClient(base_url="", model=""))
    assert "render" in facts.stage_times and "ocr" in facts.stage_times and "text_model" in facts.stage_times
    assert all(v >= 0 for v in facts.stage_times.values())  # real measurements, never negative/guessed


def test_a_run_reports_its_pages_and_stage_wall_times_to_telemetry(tmp_path):
    from pypdf import PdfWriter

    from tenderlens.cli import audit_pack
    from tenderlens.telemetry import RunTelemetry

    def make_pdf(path, n):
        w = PdfWriter()
        for _ in range(n):
            w.add_blank_page(width=400, height=500)
        with open(path, "wb") as f:
            w.write(f)
        return path

    tel, seen = RunTelemetry(), []
    audit_pack(make_pdf(tmp_path / "rfq.pdf", 1), None, make_pdf(tmp_path / "pack.pdf", 3), tmp_path, None, tmp_path / "work",
               llm=ModelClient(base_url="", model=""), vision=ModelClient(base_url="", model=""), telemetry=tel,
               progress=lambda stage, done, total: seen.append((stage, done, total)))
    snap = tel.snapshot()
    assert snap["total_pages"] == 3 and snap["processed_pages"] == 3 and snap["active_workers"] == 0
    assert {"render_ocr", "text_model", "vision_and_audit"} <= set(snap["stages"])
    assert snap["progress"]["ocr"] == [3, 3] and ("ocr", 3, 3) in seen                  # the caller's own callback still fires


def test_vision_concurrency_defaults_to_25_and_stays_within_1_to_32(monkeypatch):
    from tenderlens.llm import vision_concurrency
    monkeypatch.delenv("TENDERLENS_VISION_CONCURRENCY", raising=False)
    assert vision_concurrency() == 25
    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "500")
    assert vision_concurrency() == 32
    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "junk")
    assert vision_concurrency() == 25


def test_running_inspection_beside_the_form_readings_gives_the_same_facts_in_the_same_order(tmp_path):
    """cli._vision_and_audit now runs inspect_pages side by side with vision_extract, on a branch Facts merged
    back afterwards. The result must be exactly what running them one after the other gave: same facts, same
    order, same call counts."""
    import threading
    pages = _blank_pages(tmp_path, 3, doc_type="sbd4")
    for n in (4, 5):
        img = tmp_path / f"q{n}.png"
        Image.new("RGB", (100, 100), "white").save(img)
        pages.append(Page(n, str(img), doc_type="sbd8"))
    answer = {"sbd4.name_field": "Excel Supply Holdings", "sbd4.q2_7_state_employed": "Yes", "crossed_out": "yes",
              "overwritten": "no", "annotation": "yes", "rotated": "no", "cut_off": "no", "illegible": "no",
              "answer": "yes"}
    answers = {p.image_path: answer for p in pages}

    def snapshot(f):
        log = {k: v for k, v in f.vision_log.items() if k != "errors"}
        return [(x.field, x.value, x.page, x.source, x.verified, x.confidence) for x in f.items], log

    one_after_other = Facts()
    vision_extract(_Scripted(answers), pages, one_after_other, verify=True)
    inspect_pages(_Scripted(answers), pages, one_after_other, passes=2)

    side_by_side = Facts()
    llm = _Scripted(answers)
    branch = side_by_side.branch()
    th = threading.Thread(target=lambda: inspect_pages(llm, pages, branch, passes=2))
    th.start()
    vision_extract(llm, pages, side_by_side, verify=True)
    th.join()
    side_by_side.merge(branch)

    assert snapshot(side_by_side) == snapshot(one_after_other)
    assert any(x.field.startswith("inspect.") for x in side_by_side.items)


def test_first_readings_of_every_form_go_out_in_one_wave(tmp_path, monkeypatch):
    """All form pages are read at once, not one form type after another."""
    import threading
    import time as _time
    monkeypatch.setenv("TENDERLENS_VISION_CONCURRENCY", "25")
    pages = _blank_pages(tmp_path, 3, doc_type="sbd4")
    for n in (4, 5, 6):
        img = tmp_path / f"r{n}.png"
        Image.new("RGB", (100, 100), "white").save(img)
        pages.append(Page(n, str(img), doc_type="sbd8"))

    class Tracking(_Scripted):
        def __init__(self):
            super().__init__({})
            self.now = self.peak = 0
            self.lk = threading.Lock()

        def complete_json(self, system, user, images=None):
            with self.lk:
                self.now += 1
                self.peak = max(self.peak, self.now)
            _time.sleep(0.2)
            with self.lk:
                self.now -= 1
            return {}

    llm = Tracking()
    vision_extract(llm, pages, Facts(), verify=False)
    assert llm.peak == 6  # sbd4's 3 pages and sbd8's 3 pages in flight together


def test_one_combined_call_per_page_gives_the_same_facts_with_half_the_calls(tmp_path):
    """combined_first_pass asks the field reading and the inspection questions in one call per form page; the
    facts that come out must match the old separate calls exactly, given the same answers."""
    from tenderlens.facts import combined_first_pass
    pages = _blank_pages(tmp_path, 3, doc_type="sbd4")
    answer = {"sbd4.name_field": "Excel Supply Holdings", "sbd4.q2_7_state_employed": "Yes", "crossed_out": "yes",
              "overwritten": "no", "annotation": "no", "rotated": "no", "cut_off": "no", "illegible": "no",
              "signature_seen": "yes", "answer": "yes"}
    answers = {p.image_path: answer for p in pages}

    def snapshot(f):
        return [(x.field, x.value, x.page, x.source, x.verified, x.confidence) for x in f.items]

    separate, llm_a = Facts(), _Scripted(answers)
    vision_extract(llm_a, pages, separate, verify=True)
    inspect_pages(llm_a, pages, separate, passes=2)

    combined, llm_b = Facts(), _Scripted(answers)
    first = combined_first_pass(llm_b, pages, combined)
    vision_extract(llm_b, pages, combined, verify=True, first_answers=first)
    inspect_pages(llm_b, pages, combined, passes=2, first_answers=first)

    assert snapshot(combined) == snapshot(separate)
    assert llm_b.calls == llm_a.calls - len(pages)  # one first-reading call per page instead of two


class _ByPrompt(ModelClient):
    """Answers according to what the prompt asks, so a test can tell the focused call from the question list."""

    def __init__(self, rules):
        super().__init__(base_url="http://scripted", model="scripted")
        self.rules, self.prompts = rules, []

    def complete_json(self, system, user, images=None):
        self.calls += 1
        self.prompts.append(user)
        for needle, answer in self.rules:
            if needle in user:
                return answer
        return {}


_NO_ALL = {"crossed_out": "no", "overwritten": "no", "annotation": "no", "rotated": "no", "cut_off": "no",
           "illegible": "no", "signature_seen": "yes"}


def test_the_overwrite_question_is_asked_in_a_call_of_its_own(tmp_path):
    """On the tampered pack the six-question list said 'no' to this every time while the question asked alone said
    'yes' 5 times in 6, so it now has its own call - and the answer still ends up as an ordinary inspection finding."""
    pages = _blank_pages(tmp_path, 1, doc_type="sbd8")
    llm = _ByPrompt([("scratched out", {"answer": "yes"}), ("Answer each question", _NO_ALL)])
    facts = Facts()
    inspect_pages(llm, pages, facts, passes=1)
    assert any(f.field == "inspect.overwritten" for f in facts.items)
    list_prompt = next(p for p in llm.prompts if "Answer each question" in p)
    assert "scratched out" not in list_prompt          # not in the crowded list any more
    assert any("scratched out" in p and "Answer each question" not in p for p in llm.prompts)  # asked on its own


def test_a_failed_main_call_stays_failed_even_if_the_focused_call_answers(tmp_path):
    from tenderlens.facts import _merge_focused
    answers = {1: None, 2: {"crossed_out": "no"}}
    _merge_focused(answers, [(1, "overwritten"), (2, "overwritten")], [{"answer": "yes"}, {"answer": "yes"}])
    assert answers[1] is None and answers[2]["overwritten"] == "yes"


def test_two_model_looks_at_a_cut_off_page_are_only_a_lead_without_a_non_ai_check(tmp_path):
    pages = _blank_pages(tmp_path, 3, doc_type="sbd8")
    yes_cut = {**_NO_ALL, "cut_off": "yes"}
    llm = _ByPrompt([("Answer each question", yes_cut), ("run off the edge", {"answer": "yes"})])
    facts = Facts()
    inspect_pages(llm, pages, facts, passes=2)
    cut = [f for f in facts.items if f.field == "inspect.cut_off"]
    assert cut and all(f.verified and f.confidence < 0.8 for f in cut)  # confirmed by the model, still only a lead


def test_focused_checks_can_be_switched_off_to_save_calls(tmp_path, monkeypatch):
    """TENDERLENS_FOCUSED_CHECKS=0: the overwrite question goes back into the list, so there is no extra call."""
    import importlib
    from tenderlens import facts as FA, forms as FM
    monkeypatch.setenv("TENDERLENS_FOCUSED_CHECKS", "0")
    importlib.reload(FM)
    try:
        assert FM.FOCUSED_INSPECTIONS == ()
        qs = {k: v[0] for k, v in FM.INSPECTIONS.items()}
        assert "overwritten" in FA._main_questions(qs)
    finally:
        monkeypatch.delenv("TENDERLENS_FOCUSED_CHECKS", raising=False)
        importlib.reload(FM)
    assert FM.FOCUSED_INSPECTIONS == ("overwritten",)


def _mixed_pages(tmp_path):
    pages = _blank_pages(tmp_path, 3, doc_type="sbd4")
    for n in (4, 5):
        img = tmp_path / f"q{n}.png"
        Image.new("RGB", (100, 100), "white").save(img)
        pages.append(Page(n, str(img), doc_type="sbd8"))
    return pages


_PIPE_ANSWER = {"sbd4.name_field": "Excel Supply Holdings", "sbd4.q2_7_state_employed": "Yes", "crossed_out": "yes",
                "overwritten": "no", "annotation": "yes", "rotated": "no", "cut_off": "no", "illegible": "no",
                "answer": "yes"}


def test_second_looks_asked_as_each_page_arrives_give_the_same_facts(tmp_path):
    from tenderlens.facts import combined_first_pass

    def run(pipelined: bool):
        pages = _mixed_pages(tmp_path)
        llm, facts = _Scripted({p.image_path: _PIPE_ANSWER for p in pages}), Facts()
        ahead = {} if pipelined else None
        first = combined_first_pass(llm, pages, facts, prefetch=ahead)
        inspect_pages(llm, pages, facts, passes=2, first_answers=first, prefetched=ahead)
        vision_extract(llm, pages, facts, verify=True, first_answers=first, prefetched=ahead)
        return [(x.field, x.value, x.page, x.source, x.verified, x.confidence) for x in facts.items], llm.calls

    plain, plain_calls = run(False)
    piped, piped_calls = run(True)
    assert piped == plain
    assert any(f[0].startswith("inspect.") and f[4] for f in piped)  # second looks really confirmed something
    assert piped_calls <= plain_calls + 4  # at most a few speculative extras


def test_a_pages_second_look_does_not_wait_for_a_slow_page_to_finish_its_first_reading(tmp_path):
    from tenderlens.facts import combined_first_pass
    pages = _mixed_pages(tmp_path)
    slow = pages[1].image_path
    events: list[str] = []
    lock = threading.Lock()

    class Tracking(_Scripted):
        def complete_json(self, system, user, images=None):
            second = '"answer"' in user and "Part 1" not in user
            if images[0] == slow and not second:
                time.sleep(0.4)
            with lock:
                events.append(("second" if second else "first") + ":" + Path(images[0]).name + (":end" if images[0] == slow and not second else ""))
            return super().complete_json(system, user, images)

    llm = Tracking({p.image_path: _PIPE_ANSWER for p in pages})
    combined_first_pass(llm, pages, Facts(), prefetch={})
    assert events.index("first:" + Path(slow).name + ":end") > min(i for i, e in enumerate(events) if e.startswith("second:p1"))


def test_live_view_follows_each_page_and_counts_finished_pages(tmp_path):
    from tenderlens.facts import combined_first_pass
    from tenderlens.live import LiveState
    pages = _mixed_pages(tmp_path)
    live = LiveState()
    live.set_pages(pages, lambda dtype: dtype.upper())
    llm = _Scripted({p.image_path: _PIPE_ANSWER for p in pages})
    combined_first_pass(llm, pages, Facts(), prefetch={}, live=live)
    rows = live.snapshot()
    assert [r.number for r in rows] == [1, 2, 3, 4, 5]
    assert all(r.status == "done" for r in rows) and live.counts() == (5, 5)
    assert rows[0].form == "SBD4" and rows[3].form == "SBD8"
    assert any("crossed out" in lead.lower() for lead in rows[3].leads)  # a provisional lead from the first reading


def test_live_view_shows_a_page_that_could_not_be_read_as_finished_not_stuck(tmp_path):
    from tenderlens.facts import combined_first_pass
    from tenderlens.live import LiveState
    pages = _mixed_pages(tmp_path)
    live = LiveState()
    llm = _Scripted({p.image_path: _PIPE_ANSWER for p in pages}, fail_for={pages[2].image_path})
    combined_first_pass(llm, pages, Facts(), prefetch={}, live=live)
    assert {r.number: r.status for r in live.snapshot()}[3] == "not read"
    assert live.counts() == (5, 5)
