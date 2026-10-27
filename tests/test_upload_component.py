"""The upload widget's Python-side request handlers (_handle_request_session / _handle_upload_complete /
_handle_remove) - the actual business logic behind the browser upload flow, as plain functions over a state
dict keyed by file_id (a slot holds several files - see tenderlens/upload_component.py's module docstring),
independent of Streamlit's own component/session machinery (which AppTest can't simulate, the same
documented limitation as st.file_uploader - see tests/test_pilot.py). A fake storage backend stands in
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
    return {"items": {}, "order": [], "last_seq": -1}


def request_session(state, file_id, **msg_overrides):
    msg = {"filename": "a.pdf", "size": 1000, "content_type": "application/pdf"}
    msg.update(msg_overrides)
    upload_component._handle_request_session(state, "pack", "job-1", file_id, msg)
    return state["items"][file_id]


# --------------------------------------------------------------------------- request_session

def test_request_session_populates_the_session_for_the_browser(backend):
    state = fresh_state()
    item = request_session(state, "f1")
    assert item["session"]["session_url"] == SECRET_SESSION_URL
    assert item["session"]["object_name"].startswith("uploads/job-1/pack-")
    assert item["error"] is None
    assert state["order"] == ["f1"]


def test_request_session_too_large_sets_a_plain_language_error_not_an_exception(backend):
    state = fresh_state()
    item = request_session(state, "f1", size=storage.max_upload_bytes() + 1)
    assert item["session"] is None
    assert "limit" in item["error"].lower()
    assert SECRET_SESSION_URL not in item["error"]


def test_request_session_wrong_content_type_sets_an_error(backend):
    state = fresh_state()
    item = request_session(state, "f1", filename="a.docx", content_type="application/msword")
    assert item["session"] is None
    assert "pdf" in item["error"].lower()


def test_request_session_with_no_bucket_configured_sets_an_error_not_a_crash(monkeypatch):
    storage.set_backend_for_testing(None)
    monkeypatch.setenv("GCS_BUCKET", "")
    state = fresh_state()
    item = request_session(state, "f1")
    assert item["session"] is None
    assert item["error"]


def test_two_files_in_one_slot_are_tracked_independently(backend):
    state = fresh_state()
    item1 = request_session(state, "f1", filename="one.pdf")
    item2 = request_session(state, "f2", filename="two.pdf")
    assert state["order"] == ["f1", "f2"]
    assert item1["session"]["object_name"] != item2["session"]["object_name"]


# --------------------------------------------------------------------------- upload_complete

def test_upload_complete_verifies_and_stores_the_result(backend):
    state = fresh_state()
    item = request_session(state, "f1", size=len(PDF_BYTES))
    object_name = item["session"]["object_name"]
    backend.put_object(object_name, PDF_BYTES)
    upload_component._handle_upload_complete(state, "f1", {"object_name": object_name})
    item = state["items"]["f1"]
    assert item["result"] == {"object_name": object_name, "size": len(PDF_BYTES)}
    assert item["error"] is None
    assert item["session"] is None  # the (now-consumed) session URL must not linger in state


def test_upload_complete_rejects_a_file_that_actually_exceeds_the_limit(backend):
    """The browser-reported size at session creation was honest, but what actually landed in the bucket
    is what counts - see storage.py's module docstring on the one real enforcement point."""
    state = fresh_state()
    item = request_session(state, "f1")
    object_name = item["session"]["object_name"]
    oversized = b"%PDF-" + b"x" * (storage.max_upload_bytes() + 1)
    backend.put_object(object_name, oversized)
    upload_component._handle_upload_complete(state, "f1", {"object_name": object_name})
    item = state["items"]["f1"]
    assert item["result"] is None
    assert "limit" in item["error"].lower()
    assert object_name not in backend.objects  # deleted


def test_upload_complete_rejects_a_renamed_non_pdf(backend):
    state = fresh_state()
    item = request_session(state, "f1")
    object_name = item["session"]["object_name"]
    backend.put_object(object_name, b"not actually a pdf")
    upload_component._handle_upload_complete(state, "f1", {"object_name": object_name})
    item = state["items"]["f1"]
    assert item["result"] is None
    assert "pdf" in item["error"].lower()


def test_upload_complete_error_never_contains_the_session_url(backend):
    state = fresh_state()
    item = request_session(state, "f1")
    object_name = item["session"]["object_name"]
    backend.put_object(object_name, b"not a pdf")
    upload_component._handle_upload_complete(state, "f1", {"object_name": object_name})
    assert SECRET_SESSION_URL not in (state["items"]["f1"]["error"] or "")


def test_upload_slot_returns_verified_files_in_the_order_added(backend):
    state = fresh_state()
    item1 = request_session(state, "f1", filename="one.pdf", size=len(PDF_BYTES))
    backend.put_object(item1["session"]["object_name"], PDF_BYTES)
    upload_component._handle_upload_complete(state, "f1", {"object_name": item1["session"]["object_name"]})
    item2 = request_session(state, "f2", filename="two.pdf", size=len(PDF_BYTES))
    backend.put_object(item2["session"]["object_name"], PDF_BYTES)
    upload_component._handle_upload_complete(state, "f2", {"object_name": item2["session"]["object_name"]})
    results = upload_component._results(state)
    assert [r["object_name"] for r in results] == [state["items"]["f1"]["result"]["object_name"],
                                                    state["items"]["f2"]["result"]["object_name"]]


def test_a_failed_file_does_not_appear_in_results_but_a_later_good_one_does(backend):
    state = fresh_state()
    bad = request_session(state, "f1", size=1000)
    backend.put_object(bad["session"]["object_name"], b"not a pdf")
    upload_component._handle_upload_complete(state, "f1", {"object_name": bad["session"]["object_name"]})
    good = request_session(state, "f2", size=len(PDF_BYTES))
    backend.put_object(good["session"]["object_name"], PDF_BYTES)
    upload_component._handle_upload_complete(state, "f2", {"object_name": good["session"]["object_name"]})
    results = upload_component._results(state)
    assert len(results) == 1
    assert results[0]["object_name"] == state["items"]["f2"]["result"]["object_name"]


# --------------------------------------------------------------------------- remove

def test_remove_deletes_the_partial_object_and_drops_the_item(backend):
    state = fresh_state()
    item = request_session(state, "f1")
    object_name = item["session"]["object_name"]
    backend.put_object(object_name, b"partial bytes so far")
    upload_component._handle_remove(state, "f1")
    assert "f1" not in state["items"]
    assert "f1" not in state["order"]
    assert object_name not in backend.objects


def test_remove_deletes_an_already_finalized_object_too(backend):
    state = fresh_state()
    item = request_session(state, "f1", size=len(PDF_BYTES))
    object_name = item["session"]["object_name"]
    backend.put_object(object_name, PDF_BYTES)
    upload_component._handle_upload_complete(state, "f1", {"object_name": object_name})
    upload_component._handle_remove(state, "f1")
    assert "f1" not in state["items"]
    assert object_name not in backend.objects


def test_remove_of_an_unknown_file_id_is_a_no_op(backend):
    state = fresh_state()
    upload_component._handle_remove(state, "never-existed")  # must not raise
    assert state["items"] == {} and state["order"] == []
