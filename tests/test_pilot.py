"""Pilot access codes: every rule in isolation (no samples needed), plus one test that drives the real
Streamlit app in pilot mode.

Streamlit's AppTest cannot simulate a file_uploader widget (a documented limitation of that framework, not
something worked around here), so "runs the app in pilot mode with an upload" is split in two: a UI test
that drives the real app through code entry and confirms the transcript uploader is hidden and the gate
works, and an integration test that exercises the exact claim -> audit -> finalize/refund sequence app.py's
run button performs, using the real audit_pack pipeline when samples/ is present (skipped with a message
otherwise, like the rest of this suite).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import pilot

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "samples"


@pytest.fixture
def store(tmp_path):
    return pilot.PilotStore(tmp_path / "pilot.db")


# --------------------------------------------------------------------------- codes: shape and normalisation

def test_generated_code_shape_excludes_confusable_characters():
    display, normalised = pilot.generate_code()
    assert display.startswith("TL-") and len(normalised) == 16
    assert not (set("OIL1U0") & set(normalised) - set("0123456789"))  # no O, I, L or U letters
    for ch in normalised:
        assert ch in pilot.ALPHABET


def test_normalize_forgives_case_spaces_dashes_and_lookalikes():
    assert pilot.normalize("tl-ab3d-ef4h") == pilot.normalize("AB3D EF4H")
    assert pilot.normalize("TL-A0BD") == pilot.normalize("TL-AOBD")  # O read as 0
    assert pilot.normalize("TL-A1BD") == pilot.normalize("TL-AIBD") == pilot.normalize("TL-ALBD")  # I/L read as 1


def test_code_is_never_stored_only_its_hash(store, tmp_path):
    code = store.create_code("Jane", packs=1, days=1)
    raw_sql = (tmp_path / "pilot.db").read_bytes()
    normalised = pilot.normalize(code)
    assert normalised.encode() not in raw_sql
    assert code.encode() not in raw_sql
    # the hash itself, which IS meant to be there, proves the file was actually read
    assert pilot.hash_code(normalised).encode() in raw_sql


# --------------------------------------------------------------------------- validation outcomes

def test_wrong_code_is_reported_plainly(store):
    cs = store.validate("TL-0000-0000-0000-0000")
    assert cs.status == "wrong" and not cs.ok
    assert "isn't recognised" in cs.message()


def test_expired_code(store):
    code = store.create_code("Jane", packs=5, days=-1)  # already expired
    cs = store.validate(code)
    assert cs.status == "expired" and "expired" in cs.message()


def test_withdrawn_code(store):
    code = store.create_code("Jane", packs=5, days=10)
    store.revoke_code(code)
    cs = store.validate(code)
    assert cs.status == "withdrawn" and "withdrawn" in cs.message()


def test_last_pack_used_up(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    store.claim_pack(h, "sha-of-file-a")
    cs = store.validate(code)
    assert cs.status == "used_up" and "all been used" in cs.message()
    with pytest.raises(pilot.NoPacksLeft):
        store.claim_pack(h, "sha-of-file-b")  # a different file: genuinely a new pack, and there isn't one


def test_valid_code_reports_packs_left_and_expiry(store):
    code = store.create_code("Jane", packs=3, days=21)
    cs = store.validate(code)
    assert cs.ok and cs.packs_left == 3 and cs.packs_limit == 3
    assert f"3 of 3 packs left, valid until {cs.expires_at}." == cs.message()


# --------------------------------------------------------------------------- spending and refunding packs

def test_same_file_run_again_is_free(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    assert store.claim_pack(h, "same-file-sha") == "new"
    assert store.claim_pack(h, "same-file-sha") == "rerun"
    assert store.validate(code).packs_left == 0  # still only one pack spent


def test_a_changed_file_is_a_new_pack(store):
    code = store.create_code("Jane", packs=2, days=10)
    h = store.validate(code).code_hash
    assert store.claim_pack(h, "file-v1") == "new"
    assert store.claim_pack(h, "file-v2") == "new"  # content changed -> different sha256 -> counts again
    assert store.validate(code).packs_left == 0


def test_crashed_audit_refunds_the_pack(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    assert store.claim_pack(h, "file-a") == "new"
    assert store.validate(code).status == "used_up"
    store.refund_pack(h, "file-a")  # what app.py's except block does on any exception
    cs = store.validate(code)
    assert cs.ok and cs.packs_left == 1
    assert store.claim_pack(h, "file-a") == "new"  # the slot is really free again, not just cosmetically


def test_free_reruns_are_allowed_up_to_the_cap_then_not(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    assert store.rerun_allowed(h, "file-a")  # no reruns used yet
    for i in range(1, pilot.PilotStore.FREE_RERUN_LIMIT + 1):
        assert store.rerun_allowed(h, "file-a")
        assert store.record_rerun(h, "file-a") == i
    assert not store.rerun_allowed(h, "file-a")  # the cap (3) is reached


def test_rerun_count_is_per_file_not_per_code(store):
    code = store.create_code("Jane", packs=2, days=10)
    h = store.validate(code).code_hash
    for _ in range(pilot.PilotStore.FREE_RERUN_LIMIT):
        store.record_rerun(h, "file-a")
    assert not store.rerun_allowed(h, "file-a")
    assert store.rerun_allowed(h, "file-b")  # a different pack starts with a fresh allowance


def test_settle_pack_refunds_an_incomplete_check_and_spends_nothing(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    claim_kind = store.claim_pack(h, "file-a")
    assert claim_kind == "new"
    spent = store.settle_pack(h, "file-a", claim_kind, check_complete=False)
    assert spent is False
    cs = store.validate(code)
    assert cs.ok and cs.packs_left == 1  # the slot came back - vision never finished, so no credit was used


def test_settle_pack_finalizes_a_complete_check_and_spends_one(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    claim_kind = store.claim_pack(h, "file-a")
    spent = store.settle_pack(h, "file-a", claim_kind, check_complete=True, rfq_reference="5/2/1(0310)21/22",
                              n_issues=2)
    assert spent is True
    cs = store.validate(code)
    assert cs.status == "used_up"
    row = store.show_code(code)
    assert row["uses"][0]["rfq_reference"] == "5/2/1(0310)21/22" and row["uses"][0]["n_issues"] == 2


def test_settle_pack_never_spends_a_second_credit_on_a_free_rerun_claim(store):
    """claim_pack() returns 'rerun' for a file already claimed under this code (test_same_file_run_again_is_
    free above) - settle_pack must not spend a credit for that regardless of whether this particular
    completion succeeds."""
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    store.claim_pack(h, "file-a")
    store.finalize_pack(h, "file-a", "ref", 0)
    claim_kind = store.claim_pack(h, "file-a")
    assert claim_kind == "rerun"
    assert store.settle_pack(h, "file-a", claim_kind, check_complete=True) is False


def test_finalize_pack_records_results_without_storing_the_file(store):
    code = store.create_code("Jane", packs=1, days=10)
    h = store.validate(code).code_hash
    store.claim_pack(h, "file-a")
    store.finalize_pack(h, "file-a", "5/2/1(0310)21/22", 3)
    row = store.show_code(code)
    assert row["uses"][0]["rfq_reference"] == "5/2/1(0310)21/22" and row["uses"][0]["n_issues"] == 3


def test_two_requests_racing_for_the_last_pack_only_one_succeeds(tmp_path):
    db = tmp_path / "pilot.db"
    code = pilot.PilotStore(db).create_code("Jane", packs=1, days=10)
    h = pilot.hash_code(pilot.normalize(code))
    outcomes = []
    barrier = threading.Barrier(2)

    def attempt(file_sha):
        store = pilot.PilotStore(db)  # a separate connection, as separate requests would have
        barrier.wait()
        try:
            outcomes.append(store.claim_pack(h, file_sha))
        except pilot.NoPacksLeft:
            outcomes.append("no_packs_left")

    threads = [threading.Thread(target=attempt, args=(f"file-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["new", "no_packs_left"], outcomes
    assert pilot.PilotStore(db).validate(code).packs_left == 0


# --------------------------------------------------------------------------- admin commands (what cli.py calls)

def test_extend_adds_days_and_packs_and_reactivates(store):
    code = store.create_code("Jane", packs=1, days=5)
    store.revoke_code(code)
    assert store.validate(code).status == "withdrawn"
    before = store.show_code(code)["expires_at"]
    assert store.extend_code(code, add_days=10, add_packs=2) is True
    row = store.show_code(code)
    assert row["packs_limit"] == 3 and row["expires_at"] > before and row["revoked"] == 0
    assert store.validate(code).status == "ok"


def test_extend_and_revoke_report_unknown_codes_honestly(store):
    assert store.extend_code("TL-0000-0000-0000-0000") is False
    assert store.revoke_code("TL-0000-0000-0000-0000") is False


def test_list_and_show_reflect_usage(store):
    c1 = store.create_code("Alice", packs=2, days=10)
    store.create_code("Bob", packs=1, days=10)
    h1 = store.validate(c1).code_hash
    store.claim_pack(h1, "f1")
    rows = {r["name"]: r for r in store.list_codes()}
    assert rows["Alice"]["used"] == 1 and rows["Bob"]["used"] == 0
    assert store.show_code(c1)["used"] == 1


# --------------------------------------------------------------------------- the lockout

def test_five_wrong_tries_in_a_row_locks_for_a_minute():
    state = {}
    for _ in range(4):
        pilot.record_attempt(state, ok=False)
        assert pilot.lockout_remaining(state) == 0
    pilot.record_attempt(state, ok=False)  # the 5th
    remaining = pilot.lockout_remaining(state)
    assert 0 < remaining <= pilot.LOCKOUT_SECONDS


def test_a_correct_code_resets_the_wrong_count():
    state = {}
    for _ in range(4):
        pilot.record_attempt(state, ok=False)
    pilot.record_attempt(state, ok=True)
    assert state.get("pilot_fail_count", 0) == 0
    for _ in range(4):  # back to 4 more wrongs: still not 5 *in a row*
        pilot.record_attempt(state, ok=False)
    assert pilot.lockout_remaining(state) == 0


# --------------------------------------------------------------------------- file retention

def test_old_files_are_deleted_young_ones_are_not(tmp_path):
    import os
    import time

    old = tmp_path / "uploads" / "old.pdf"
    new = tmp_path / "uploads" / "new.pdf"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    stale = time.time() - 25 * 3600  # 25h ago: past the 24h promise
    os.utime(old, (stale, stale))
    removed = pilot.purge_old_files([tmp_path], max_age_hours=24)
    assert removed == 1
    assert not old.exists() and new.exists()


def test_purge_ignores_a_missing_directory():
    assert pilot.purge_old_files(["/no/such/directory"], max_age_hours=24) == 0


def test_pilot_terms_is_unchanged_without_a_hosting_note(monkeypatch):
    monkeypatch.delenv("TENDERLENS_HOSTING_NOTE", raising=False)
    assert pilot.pilot_terms() == pilot.PILOT_TERMS  # the Streamlit Community Cloud deployment is unaffected


def test_pilot_terms_appends_the_hosting_note_when_set(monkeypatch):
    monkeypatch.setenv("TENDERLENS_HOSTING_NOTE",
                       "Documents are processed on Google Cloud (Johannesburg); page images are sent to "
                       "DeepInfra for reading.")
    terms = pilot.pilot_terms()
    assert terms.startswith(pilot.PILOT_TERMS)
    assert "Johannesburg" in terms and "DeepInfra" in terms


# --------------------------------------------------------------------------- the real Streamlit app, pilot mode

def test_app_in_pilot_mode_gates_upload_and_hides_transcript(tmp_path, monkeypatch):
    """Runs the actual app.py. Cannot simulate the file_uploader widgets themselves (AppTest does not support
    that), so this checks everything around it: the code gate renders, a wrong code is rejected with a
    message, a valid one shows packs-left, and the transcript uploader - which only exists in this pilot
    branch - is not on the page."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TENDERLENS_PILOT", "1")
    monkeypatch.setenv("TENDERLENS_PILOT_DB", str(tmp_path / "pilot.db"))
    # app.py's sidebar calls system_check(), which pings whatever endpoint is configured - blank it so this
    # test never makes a real network call (slow, and not free) regardless of what's in .env.
    for k in ("TENDERLENS_LLM_BASE_URL", "TENDERLENS_LLM_MODEL", "TENDERLENS_LLM_API_KEY",
             "TENDERLENS_VISION_BASE_URL", "TENDERLENS_VISION_MODEL", "TENDERLENS_VISION_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    code = pilot.PilotStore(tmp_path / "pilot.db").create_code("Contractor", packs=2, days=5)

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    uploader_labels = [u.label for u in at.get("file_uploader")]
    assert "RFQ/Tender documents (PDF)" in uploader_labels and "Response pack (PDF, scans are fine)" in uploader_labels
    assert not any("transcript" in lbl.lower() for lbl in uploader_labels)
    assert any(b.label == "Audit the pack" and b.disabled for b in at.button)  # no files yet, even with a code

    at.text_input(key="pilot_code").set_value("TL-WRONG-CODE-0000-0000").run()
    assert any("isn't recognised" in e.value for e in at.error)

    at.text_input(key="pilot_code").set_value(code).run()
    assert any("2 of 2 packs left" in s.value for s in at.success)


def test_pilot_integration_claim_audit_finalize(tmp_path):
    """The exact sequence app.py's run button performs, with the real pipeline - but llm/vision are forced to
    unconfigured clients (rules + OCR only), so this never touches a real endpoint or spends real money
    regardless of what's in .env. That also means it does not assert 0 issues: without a vision model or a
    transcript, handwritten entries are not read the same way the winning pack's own report was produced, so
    the count here is whatever rules + OCR alone give it, not the full pipeline's result.
    Skipped without samples/ - the pilot bookkeeping itself is already fully covered above without needing them."""
    if not (S / "response" / "pack.pdf").is_file():
        pytest.skip("samples/response/pack.pdf not present (git-ignored, not shipped with the code)")
    from tenderlens.cli import audit_pack
    from tenderlens.llm import LlamaClient

    store = pilot.PilotStore(tmp_path / "pilot.db")
    code = store.create_code("Contractor", packs=1, days=5)
    h = store.validate(code).code_hash
    pack = S / "response" / "pack.pdf"
    file_sha = pilot.file_sha256(pack)
    transcript = S / "response" / "pack.transcript.json"

    assert store.claim_pack(h, file_sha) == "new"
    req, res, facts, pages = audit_pack(S / "rfq" / "rfq_email.pdf", S / "rfq" / "spec_0310.pdf", pack, S / "blank",
                                        transcript if transcript.is_file() else None,
                                        llm=LlamaClient(base_url="", model=""),
                                        vision=LlamaClient(base_url="", model=""))
    n_issues = sum(1 for f in res.findings if f.tier == "issue")
    store.finalize_pack(h, file_sha, req.reference, n_issues)

    row = store.show_code(code)
    assert row["used"] == 1 and row["uses"][0]["n_issues"] == n_issues and row["uses"][0]["rfq_reference"] == req.reference
    assert store.validate(code).status == "used_up"
