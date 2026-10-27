"""facts._ask()'s vision-cache integration: with facts.job_id set and GCS configured, an identical
(prompt, images) pair is answered once and the second call is a cache hit, not a second real call - this is
what makes a resumed job skip pages already answered instead of repeating paid calls (see vision_cache.py's
and facts._ask()'s own docstrings). Without a job_id (every existing caller), behaviour is unchanged: every
call is real. A fake LlamaClient and a fake GCS backend stand in throughout - no network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import storage
from tenderlens.facts import Facts, _ask
from tenderlens.llm import LlamaClient
from tests.test_storage import FakeBackend


class CountingLLM(LlamaClient):
    """Returns a fixed answer and counts how many times it was actually asked - the thing a cache hit must
    avoid incrementing."""

    def __init__(self, answer=None):
        super().__init__(base_url="http://fake", model="fake-vision")
        self.answer = answer if answer is not None else {"answer": "yes"}
        self.real_calls = 0

    def complete_json(self, system, user, images=None):
        self.real_calls += 1
        return dict(self.answer)


@pytest.fixture
def gcs(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "fake-test-bucket")
    b = FakeBackend()
    storage.set_backend_for_testing(b)
    yield b
    storage.set_backend_for_testing(None)


def make_image(tmp_path, content=b"page image bytes"):
    p = tmp_path / "p1.png"
    p.write_bytes(content)
    return str(p)


# --------------------------------------------------------------------------- without a job_id: unchanged

def test_without_a_job_id_every_call_is_real(tmp_path):
    facts = Facts()
    llm = CountingLLM()
    img = make_image(tmp_path)
    _ask(llm, facts, "read the tick box", [img], page_no=1)
    _ask(llm, facts, "read the tick box", [img], page_no=1)
    assert llm.real_calls == 2
    assert facts.vision_log["cache_hits"] == 0


def test_without_gcs_configured_every_call_is_real_even_with_a_job_id(monkeypatch, tmp_path):
    monkeypatch.setenv("GCS_BUCKET", "")
    facts = Facts()
    facts.job_id = "job-1"
    llm = CountingLLM()
    img = make_image(tmp_path)
    _ask(llm, facts, "read the tick box", [img], page_no=1)
    _ask(llm, facts, "read the tick box", [img], page_no=1)
    assert llm.real_calls == 2


# --------------------------------------------------------------------------- with a job_id: resumable

def test_an_identical_repeat_call_is_a_cache_hit_not_a_second_real_call(gcs, tmp_path):
    facts = Facts()
    facts.job_id = "job-1"
    llm = CountingLLM({"answer": "yes"})
    img = make_image(tmp_path)

    first = _ask(llm, facts, "read the tick box", [img], page_no=1)
    second = _ask(llm, facts, "read the tick box", [img], page_no=1)

    assert first == second == {"answer": "yes"}
    assert llm.real_calls == 1  # the second _ask() never reached complete_json
    assert facts.vision_log["cache_hits"] == 1
    assert facts.vision_log["calls"] == 2  # still counted as two questions asked, just one of them free


def test_simulated_restart_a_fresh_facts_and_llm_still_gets_the_cached_answer(gcs, tmp_path):
    """The actual resumability scenario: the first Facts/LlamaClient are gone (the instance restarted), but
    the job_id and the cache in the bucket survive - a brand new Facts() picks up right where it left off."""
    img = make_image(tmp_path)
    job_id = "job-1"

    facts_before = Facts()
    facts_before.job_id = job_id
    llm_before = CountingLLM({"answer": "yes"})
    _ask(llm_before, facts_before, "read the tick box", [img], page_no=1)
    assert llm_before.real_calls == 1

    # simulate a Cloud Run instance restart: brand new process-local objects, same job_id, same bucket
    facts_after = Facts()
    facts_after.job_id = job_id
    llm_after = CountingLLM({"answer": "yes"})
    result = _ask(llm_after, facts_after, "read the tick box", [img], page_no=1)

    assert result == {"answer": "yes"}
    assert llm_after.real_calls == 0  # never had to ask the model at all
    assert facts_after.vision_log["cache_hits"] == 1


def test_a_different_page_is_still_a_real_call_not_a_false_hit(gcs, tmp_path):
    facts = Facts()
    facts.job_id = "job-1"
    llm = CountingLLM({"answer": "yes"})
    img1 = make_image(tmp_path, content=b"page one")
    img2_path = tmp_path / "p2.png"
    img2_path.write_bytes(b"page two - different bytes")

    _ask(llm, facts, "read the tick box", [img1], page_no=1)
    _ask(llm, facts, "read the tick box", [str(img2_path)], page_no=2)

    assert llm.real_calls == 2
    assert facts.vision_log["cache_hits"] == 0


def test_a_failed_call_is_never_cached(gcs, tmp_path):
    """A None result (the model errored) must not be stored - a later retry (same job, same page) should
    get a real second attempt, not a cached failure standing in forever."""
    class FailingThenOK(LlamaClient):
        def __init__(self):
            super().__init__(base_url="http://fake", model="fake-vision")
            self.attempt = 0

        def complete_json(self, system, user, images=None):
            self.attempt += 1
            if self.attempt == 1:
                raise OSError("connection refused")
            return {"answer": "yes"}

    facts = Facts()
    facts.job_id = "job-1"
    llm = FailingThenOK()
    img = make_image(tmp_path)

    first = _ask(llm, facts, "read the tick box", [img], page_no=1)
    assert first is None
    assert facts.vision_log["failed"] == 1

    second = _ask(llm, facts, "read the tick box", [img], page_no=1)
    assert second == {"answer": "yes"}  # a real retry, not a cached None
    assert llm.attempt == 2
