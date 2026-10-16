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
from tenderlens.llm import LlamaClient, run_concurrent


def client(m, **kw):
    return LlamaClient(base_url=m.url, model="scripted", api_key="sk-test", **kw)


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

class _Scripted(LlamaClient):
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
                                        llm=LlamaClient(base_url="", model=""),
                                        vision=LlamaClient(base_url="", model=""))
    assert "render" in facts.stage_times and "ocr" in facts.stage_times and "text_model" in facts.stage_times
    assert all(v >= 0 for v in facts.stage_times.values())  # real measurements, never negative/guessed
