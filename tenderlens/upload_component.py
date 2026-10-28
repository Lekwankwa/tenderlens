"""Python side of the browser-to-bucket upload widget (tenderlens/static/upload_widget/index.html).

The widget's actual file bytes never touch this process - only small JSON messages cross the bridge
(filename, size, content type, a GCS object name, a session URL). The session URL is a bearer credential
(see storage.py's module docstring): it lives in st.session_state for as long as an upload is in progress,
is handed to the component as a render argument so the browser can PUT to it, and is never logged, printed,
or included in anything shown to the user beyond that.

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


def upload_slot(label: str, kind: str, job_id: str, key: str) -> dict | None:
    """Renders one upload slot (file picker, progress bar, resume/cancel). Call it on every rerun with the
    same `key` - the component instance, and the in-browser File object it's holding, survive reruns as
    long as the key doesn't change.

    Returns {"object_name": str, "size": int} once storage.finalize_upload() has verified the upload
    server-side (real size, real PDF magic bytes - not anything the browser claimed), or None until then.
    """
    state_key = f"_upload_slot_{key}"
    state = st.session_state.setdefault(state_key, {"session": None, "error": None, "result": None,
                                                     "last_seq": -1})

    session = state["session"]
    returned = _component(
        label=label,
        kind=kind,
        max_bytes=storage.max_upload_bytes(),
        session_url=session["session_url"] if session else None,
        object_name=session["object_name"] if session else None,
        error=state["error"],
        verified=state["result"],
        key=key,
    )

    if not returned or not isinstance(returned, dict):
        return state["result"]
    seq = returned.get("_seq")
    if seq is None or seq <= state["last_seq"]:
        return state["result"]  # already handled this message, or it's stale - see module docstring
    state["last_seq"] = seq

    action = returned.get("action")
    if action == "request_session":
        _handle_request_session(state, kind, job_id, returned)
        st.rerun()
    elif action == "upload_complete":
        _handle_upload_complete(state, returned)
        st.rerun()
    elif action == "cancelled":
        _handle_cancelled(state)
        st.rerun()
    return state["result"]


def _handle_request_session(state: dict, kind: str, job_id: str, msg: dict) -> None:
    try:
        session = storage.create_upload_session(kind, msg.get("filename", ""), int(msg.get("size", 0)),
                                                 msg.get("content_type", ""), job_id)
    except storage.StorageError as err:
        state["error"], state["session"], state["result"] = str(err), None, None
        return
    except Exception:  # noqa: BLE001 - a network/library failure here must become a visible retry prompt,
        # never an uncaught exception: that would crash the whole Streamlit rerun (the WHOLE page, since this
        # runs synchronously), leaving the widget's "Starting upload..." stuck with no way out but a reload.
        state["error"] = "Could not start the upload. Check your connection and try again."
        state["session"], state["result"] = None, None
        return
    # session.session_url is a bearer credential - held only in session_state and the component's render
    # args, never logged. Only object_name (server-generated, no user data) would ever be safe to log.
    state["session"] = {"session_url": session.session_url, "object_name": session.object_name}
    state["error"] = None
    # A new session means a new file was picked to replace a previously-verified one - without clearing
    # this, the widget kept re-rendering the OLD result (args.verified stays truthy) instead of showing the
    # new upload's progress, since onRender() checks args.verified before it checks for a session_url.
    state["result"] = None


def _handle_upload_complete(state: dict, msg: dict) -> None:
    object_name = msg.get("object_name", "")
    try:
        verified = storage.finalize_upload(object_name)
    except storage.StorageError as err:
        state["error"], state["result"] = str(err), None
    else:
        state["result"], state["error"] = verified, None
    state["session"] = None


def _handle_cancelled(state: dict) -> None:
    session = state["session"]
    if session:
        try:
            storage.delete(session["object_name"])  # best-effort: don't leave a partial object behind
        except storage.StorageError:
            pass  # a stray partial upload is cleaned up by the bucket's retention lifecycle rule regardless
    state["session"], state["error"], state["result"] = None, None, None
