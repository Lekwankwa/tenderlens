"""Job-state persistence: a no-op without a job_id or GCS configured (today's exact behaviour for the
small-file path), a correct save/load/clear round-trip when configured, and that a save failure never
raises. A fake GCS backend stands in throughout - no emulator, no network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import jobs, storage
from tests.test_storage import FakeBackend


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "fake-test-bucket")
    b = FakeBackend()
    storage.set_backend_for_testing(b)
    yield b
    storage.set_backend_for_testing(None)


# --------------------------------------------------------------------------- no-op paths

def test_save_without_a_job_id_is_a_no_op(backend):
    jobs.save("", "running", stage="vision_first", done=1, total=10)
    assert backend.objects == {}


def test_load_without_a_job_id_returns_none(backend):
    assert jobs.load("") is None


def test_load_without_gcs_configured_returns_none(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "")
    assert jobs.load("job-1") is None


def test_load_for_a_job_that_was_never_saved_returns_none(backend):
    assert jobs.load("never-saved") is None


# --------------------------------------------------------------------------- round-trip

def test_save_then_load_round_trips(backend):
    jobs.save("job-1", "running", stage="vision_checks", done=7, total=20)
    state = jobs.load("job-1")
    assert state["status"] == "running"
    assert state["stage"] == "vision_checks"
    assert state["done"] == 7
    assert state["total"] == 20
    assert "updated_at" in state


def test_save_rejects_an_unknown_status(backend):
    with pytest.raises(ValueError):
        jobs.save("job-1", "sort-of-done")


def test_a_later_save_overwrites_the_earlier_one(backend):
    jobs.save("job-1", "running", stage="ocr", done=1, total=30)
    jobs.save("job-1", "running", stage="vision_first", done=15, total=30)
    state = jobs.load("job-1")
    assert state["stage"] == "vision_first" and state["done"] == 15


def test_different_jobs_do_not_collide(backend):
    jobs.save("job-1", "running", stage="ocr", done=1, total=10)
    jobs.save("job-2", "done", stage="", done=10, total=10)
    assert jobs.load("job-1")["status"] == "running"
    assert jobs.load("job-2")["status"] == "done"


def test_clear_removes_the_job_state(backend):
    jobs.save("job-1", "done", done=10, total=10)
    jobs.clear("job-1")
    assert jobs.load("job-1") is None


def test_clear_without_a_job_id_is_a_no_op(backend):
    jobs.clear("")  # must not raise


# --------------------------------------------------------------------------- failure tolerance

def test_save_never_raises_even_if_the_backend_write_fails(backend, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network blip")
    monkeypatch.setattr(backend, "write_bytes", boom)
    jobs.save("job-1", "running", stage="ocr", done=1, total=10)  # must not raise
