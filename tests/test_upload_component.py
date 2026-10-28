"""The upload widget's Python-side request handlers (_handle_request_session / _handle_upload_complete /
_handle_cancelled) - the actual business logic behind the browser upload flow, as plain functions over a
state dict, independent of Streamlit's own component/session machinery (which AppTest can't simulate, the
same documented limitation as st.file_uploader - see tests/test_pilot.py). A fake storage backend stands in
throughout: no GCS emulator, no network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import storage, upload_component
from tests.test_storage import FakeBackend, SECRET_SESSION_URL, PDF_BYTES


@pytest.fixture
def backend():
    b = FakeBackend()
    storage.set_backend_for_testing(b)
    yield b
    storage.set_backend_for_testing(None)


def fresh_state():
    return {"session": None, "error": None, "result": None, "last_seq": -1}


# --------------------------------------------------------------------------- request_session

def test_request_session_populates_the_session_for_the_browser(backend):
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"})
    assert state["session"]["session_url"] == SECRET_SESSION_URL
    assert state["session"]["object_name"].startswith("uploads/job-1/pack-")
    assert state["error"] is None


def test_request_session_too_large_sets_a_plain_language_error_not_an_exception(backend):
    state = fresh_state()
    upload_component._handle_request_session(
        state, "pack", "job-1",
        {"filename": "a.pdf", "size": storage.max_upload_bytes() + 1, "content_type": "application/pdf"})
    assert state["session"] is None
    assert "limit" in state["error"].lower()
    assert SECRET_SESSION_URL not in state["error"]


def test_request_session_wrong_content_type_sets_an_error(backend):
    state = fresh_state()
    upload_component._handle_request_session(
        state, "pack", "job-1", {"filename": "a.docx", "size": 1000, "content_type": "application/msword"})
    assert state["session"] is None
    assert "pdf" in state["error"].lower()


def test_request_session_with_no_bucket_configured_sets_an_error_not_a_crash(monkeypatch):
    storage.set_backend_for_testing(None)
    monkeypatch.setenv("GCS_BUCKET", "")
    state = fresh_state()
    upload_component._handle_request_session(
        state, "pack", "job-1", {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"})
    assert state["session"] is None
    assert state["error"]


def test_request_session_clears_a_previous_result(backend):
    """Picking a second, bigger file after one was already verified must not leave the old verified
    result sitting in state - the widget's onRender() treats a truthy `verified` as "nothing to do",
    which left the UI stuck showing the old file's result instead of the new upload's progress."""
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": len(PDF_BYTES),
                                               "content_type": "application/pdf"})
    object_name = state["session"]["object_name"]
    backend.put_object(object_name, PDF_BYTES)
    upload_component._handle_upload_complete(state, {"object_name": object_name})
    assert state["result"] is not None

    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "b.pdf", "size": 2000, "content_type": "application/pdf"})
    assert state["result"] is None


# --------------------------------------------------------------------------- upload_complete

def test_upload_complete_verifies_and_stores_the_result(backend):
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": len(PDF_BYTES),
                                               "content_type": "application/pdf"})
    object_name = state["session"]["object_name"]
    backend.put_object(object_name, PDF_BYTES)
    upload_component._handle_upload_complete(state, {"object_name": object_name})
    assert state["result"] == {"object_name": object_name, "size": len(PDF_BYTES)}
    assert state["error"] is None
    assert state["session"] is None  # the (now-consumed) session URL must not linger in state


def test_upload_complete_rejects_a_file_that_actually_exceeds_the_limit(backend):
    """The browser-reported size at session creation was honest, but what actually landed in the bucket
    is what counts - see storage.py's module docstring on the one real enforcement point."""
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"})
    object_name = state["session"]["object_name"]
    oversized = b"%PDF-" + b"x" * (storage.max_upload_bytes() + 1)
    backend.put_object(object_name, oversized)
    upload_component._handle_upload_complete(state, {"object_name": object_name})
    assert state["result"] is None
    assert "limit" in state["error"].lower()
    assert object_name not in backend.objects  # deleted


def test_upload_complete_rejects_a_renamed_non_pdf(backend):
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"})
    object_name = state["session"]["object_name"]
    backend.put_object(object_name, b"not actually a pdf")
    upload_component._handle_upload_complete(state, {"object_name": object_name})
    assert state["result"] is None
    assert "pdf" in state["error"].lower()


def test_upload_complete_error_never_contains_the_session_url(backend):
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"})
    object_name = state["session"]["object_name"]
    backend.put_object(object_name, b"not a pdf")
    upload_component._handle_upload_complete(state, {"object_name": object_name})
    assert SECRET_SESSION_URL not in (state["error"] or "")


# --------------------------------------------------------------------------- cancelled

def test_cancelled_deletes_the_partial_object_and_clears_state(backend):
    state = fresh_state()
    upload_component._handle_request_session(state, "pack", "job-1",
                                              {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"})
    object_name = state["session"]["object_name"]
    backend.put_object(object_name, b"partial bytes so far")
    upload_component._handle_cancelled(state)
    assert state["session"] is None and state["result"] is None and state["error"] is None
    assert object_name not in backend.objects


def test_cancelled_before_any_session_was_created_is_a_no_op(backend):
    state = fresh_state()
    upload_component._handle_cancelled(state)  # must not raise even with nothing to clean up
    assert state["session"] is None
