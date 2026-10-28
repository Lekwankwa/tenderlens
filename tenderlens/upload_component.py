"""Python side of the browser-to-bucket upload widget (tenderlens/static/upload_widget/index.html).

The widget's actual file bytes never touch this process - only small JSON messages cross the bridge
(filename, size, content type, a GCS object name, a session URL). A session URL is a bearer credential
(see storage.py's module docstring): it lives in st.session_state for as long as its upload is in progress,
is handed to the component as a render argument so the browser can PUT to it, and is never logged, printed,
or included in anything shown to the user beyond that.

A slot can hold several files (up to storage.py's own limits per file; the widget's own UI cap is passed as
max_files below) - each tracked independently by a `file_id` the frontend assigns, so one slow or failed
upload never blocks the bookkeeping for another file in the same slot (only the single-flight exchange with
Python is serialised - see the widget's own file header comment).

Round-trip design: a Streamlit custom component's returned value persists across reruns until the frontend
sends a new one, so each message from JS carries a `_seq` the frontend increments - this module only acts
on a `_seq` it hasn't already handled, or it would re-create a session (or re-finalize an already-finalized
upload) on every unrelated rerun of the page.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from . import storage

_COMPONENT_DIR = Path(__file__).parent / "static" / "upload_widget"
_component = components.declare_component("tenderlens_upload", path=str(_COMPONENT_DIR))
_MAX_FILES = 10  # matches the old st.file_uploader(accept_multiple_files=True) slots this widget replaces


def upload_slot(label: str, kind: str, job_id: str, key: str) -> list[dict]:
    """Renders one upload slot (file picker, a list of in-progress/finished files with resume/cancel/remove
    each). Call it on every rerun with the same `key` - the component instance, and any in-browser File
    objects it's holding, survive reruns as long as the key doesn't change.

    Returns the files storage.finalize_upload() has verified server-side so far (real size, real PDF magic
    bytes - not anything the browser claimed), in the order they were added: a list of
    {"object_name": str, "size": int}, possibly empty.
    """
    state_key = f"_upload_slot_{key}"
    state = st.session_state.setdefault(state_key, {"items": {}, "order": [], "last_seq": -1})

    returned = _component(
        label=label,
        kind=kind,
        max_bytes=storage.max_upload_bytes(),
        max_files=_MAX_FILES,
        files=[_file_arg(state, fid) for fid in state["order"]],
        key=key,
    )

    if not returned or not isinstance(returned, dict):
        return _results(state)
    seq = returned.get("_seq")
    if seq is None or seq <= state["last_seq"]:
        return _results(state)  # already handled this message, or it's stale - see module docstring
    state["last_seq"] = seq

    action = returned.get("action")
    file_id = returned.get("file_id")
    if action == "request_session" and file_id:
        _handle_request_session(state, kind, job_id, file_id, returned)
        st.rerun()
    elif action == "upload_complete" and file_id:
        _handle_upload_complete(state, file_id, returned)
        st.rerun()
    elif action == "remove" and file_id:
        _handle_remove(state, file_id)
        st.rerun()
    return _results(state)


def _file_arg(state: dict, file_id: str) -> dict:
    item = state["items"][file_id]
    session = item["session"]
    return {"file_id": file_id, "session_url": session["session_url"] if session else None,
            "object_name": session["object_name"] if session else None, "error": item["error"],
            "verified": item["result"]}


def _results(state: dict) -> list[dict]:
    return [state["items"][fid]["result"] for fid in state["order"] if state["items"][fid]["result"]]


def _set_item(state: dict, file_id: str, **fields) -> None:
    item = state["items"].setdefault(file_id, {"session": None, "error": None, "result": None})
    item.update(fields)
    if file_id not in state["order"]:
        state["order"].append(file_id)


def _handle_request_session(state: dict, kind: str, job_id: str, file_id: str, msg: dict) -> None:
    try:
        session = storage.create_upload_session(kind, msg.get("filename", ""), int(msg.get("size", 0)),
                                                 msg.get("content_type", ""), job_id)
    except storage.StorageError as err:
        _set_item(state, file_id, error=str(err), session=None, result=None)
        return
    except Exception:  # noqa: BLE001 - a network/library failure here must become a visible retry prompt,
        # never an uncaught exception: that would crash the whole Streamlit rerun (the WHOLE page, since this
        # runs synchronously), leaving the widget's "Starting upload..." stuck with no way out but a reload.
        _set_item(state, file_id, error="Could not start the upload. Check your connection and try again.",
                  session=None, result=None)
        return
    # session.session_url is a bearer credential - held only in session_state and the component's render
    # args, never logged. Only object_name (server-generated, no user data) would ever be safe to log.
    _set_item(state, file_id, session={"session_url": session.session_url, "object_name": session.object_name},
              error=None)


def _handle_upload_complete(state: dict, file_id: str, msg: dict) -> None:
    object_name = msg.get("object_name", "")
    try:
        verified = storage.finalize_upload(object_name)
    except storage.StorageError as err:
        _set_item(state, file_id, error=str(err), result=None)
    else:
        _set_item(state, file_id, result=verified, error=None)
    item = state["items"].get(file_id)
    if item:
        item["session"] = None


def _handle_remove(state: dict, file_id: str) -> None:
    item = state["items"].pop(file_id, None)
    if item:
        object_name = None
        if item.get("session"):
            object_name = item["session"]["object_name"]
        elif item.get("result"):
            object_name = item["result"]["object_name"]
        if object_name:
            try:
                storage.delete(object_name)  # best-effort: don't leave an orphaned object behind
            except storage.StorageError:
                pass  # a stray object is cleaned up by the bucket's own retention lifecycle rule regardless
    if file_id in state["order"]:
        state["order"].remove(file_id)
