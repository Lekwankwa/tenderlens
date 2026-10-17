"""Regression test for a real bug found live: app.py's progress callback (app_progress) is invoked from
OCR/vision worker threads (the parallel OCR thread pool, the vision run_concurrent thread pool), but
Streamlit only attaches its script-run context to the main thread. An st.* call from a thread without that
context raises streamlit.errors.NoSessionContext - which is exactly what crashed a real audit run mid-way
through "vision first readings". app.py's fix is to capture get_script_run_ctx() on the main thread before
the run and re-attach it (add_script_run_ctx) on every progress callback, regardless of which worker thread
calls it. This test exercises that exact pattern (not app.py itself, which AppTest cannot drive end-to-end
since it cannot simulate file_uploader - see test_pilot.py) through a minimal script run via AppTest.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT = '''
import threading
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

line = st.empty()
main_ctx = get_script_run_ctx()

def progress(i):
    add_script_run_ctx(threading.current_thread(), main_ctx)
    line.caption(f"done {i}")

with ThreadPoolExecutor(max_workers=3) as ex:
    list(ex.map(progress, range(5)))

st.write("ok")
'''


def test_a_progress_callback_from_a_worker_thread_does_not_raise_nosessioncontext(tmp_path):
    from streamlit.testing.v1 import AppTest

    script = tmp_path / "repro.py"
    script.write_text(SCRIPT)
    at = AppTest.from_file(str(script), default_timeout=30).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.caption  # the worker-thread st.* call actually went through, not silently skipped
