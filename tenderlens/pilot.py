"""Pilot access codes: gate the app behind one-code-per-contractor invites, without storing anything about
the documents they audit.

Turned on with the environment variable TENDERLENS_PILOT=1 (see app.py and README.md). When it is off,
nothing in this module is consulted and the app behaves exactly as it always has.

A code looks like TL-XXXX-XXXX-XXXX-XXXX, drawn from Crockford's base32 alphabet (digits and letters, with
O, I, L and U left out because they are easily confused with 0, 1 and each other). We never store the code
itself, only a SHA-256 hash of its normalised form, so a leaked database cannot be turned back into codes.

What is stored, per code: a name, an optional email, a pack limit, an expiry date, and whether it has been
withdrawn. Per pack used: the code's hash, a SHA-256 fingerprint of the file (not the file), the RFQ
reference string, the number of issues found, and a timestamp. Never the file, its pages, or anything read
from it. See purge_old_files for why the files themselves do not linger either.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no O, I, L or U
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})
PREFIX = "TL"
GROUP_LEN, N_GROUPS = 4, 4
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def pilot_enabled() -> bool:
    return os.environ.get("TENDERLENS_PILOT", "") == "1"


def db_path() -> Path:
    return Path(os.environ.get("TENDERLENS_PILOT_DB", "pilot/pilot.db"))


# --------------------------------------------------------------------------- codes

def generate_code() -> tuple[str, str]:
    """(display, normalised). Display is shown to the operator once; normalised is what gets hashed."""
    chars = [secrets.choice(ALPHABET) for _ in range(GROUP_LEN * N_GROUPS)]
    normalised = "".join(chars)
    groups = ["".join(chars[i:i + GROUP_LEN]) for i in range(0, len(chars), GROUP_LEN)]
    return f"{PREFIX}-" + "-".join(groups), normalised


def normalize(raw: str) -> str:
    """Case, spaces and dashes don't matter; O/I/L are read as the digit or letter they're usually meant to be."""
    s = re.sub(r"[\s-]", "", (raw or "").strip().upper())
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
    return s.translate(_LOOKALIKES)


def hash_code(normalised: str) -> str:
    return hashlib.sha256(normalised.encode()).hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class NoPacksLeft(Exception):
    pass


class UnknownCode(Exception):
    pass


@dataclass
class CodeStatus:
    status: str  # wrong | withdrawn | expired | used_up | ok
    code_hash: str = ""
    name: str = ""
    packs_limit: int = 0
    packs_left: int = 0
    expires_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def message(self) -> str:
        return {
            "wrong": "That code isn't recognised. Check for typos and try again.",
            "withdrawn": "This code has been withdrawn. Contact whoever gave it to you.",
            "expired": f"This code expired on {self.expires_at}. Contact whoever gave it to you for a new one.",
            "used_up": f"This code's {self.packs_limit} pack(s) have all been used. Contact whoever gave it to you.",
            "ok": f"{self.packs_left} of {self.packs_limit} packs left, valid until {self.expires_at}.",
        }[self.status]


@dataclass
class PackUse:
    code_hash: str
    file_sha256: str
    rfq_reference: str = ""
    n_issues: int | None = None
    created_at: str = ""


class PilotStore:
    """One SQLite file. A fresh connection per operation (and `BEGIN IMMEDIATE`) is what makes
    claim_pack() safe when two requests race for the last pack: whichever gets the write lock first
    commits, and the second sees the now-updated count before it decides."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._ro() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS codes (
                    code_hash TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT,
                    packs_limit INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS pack_uses (
                    code_hash TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    rfq_reference TEXT,
                    n_issues INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (code_hash, file_sha256)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _ro(self):
        """A connection for reads, closed when the block exits (sqlite3's own `with con:` commits/rolls
        back but does NOT close the connection, so this isn't that)."""
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _tx(self):
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    # -- creating and administering codes -----------------------------------
    def create_code(self, name: str, email: str = "", packs: int = 3, days: int = 21) -> str:
        """Returns the display code. This is the only time it exists outside the operator's hands."""
        display, normalised = generate_code()
        h = hash_code(normalised)
        expires = (date.today() + timedelta(days=days)).isoformat()
        with self._tx() as con:
            con.execute("INSERT INTO codes(code_hash, name, email, packs_limit, created_at, expires_at, revoked) "
                       "VALUES (?,?,?,?,?,?,0)", (h, name, email or None, packs, _now(), expires))
        return display

    def _code_row(self, con, code_hash: str):
        return con.execute("SELECT * FROM codes WHERE code_hash=?", (code_hash,)).fetchone()

    def _uses_count(self, con, code_hash: str) -> int:
        return con.execute("SELECT COUNT(*) FROM pack_uses WHERE code_hash=?", (code_hash,)).fetchone()[0]

    def validate(self, raw_code: str) -> CodeStatus:
        h = hash_code(normalize(raw_code))
        with self._ro() as con:
            row = self._code_row(con, h)
            if row is None:
                return CodeStatus("wrong")
            if row["revoked"]:
                return CodeStatus("withdrawn", code_hash=h, name=row["name"], expires_at=row["expires_at"])
            if date.today().isoformat() > row["expires_at"]:
                return CodeStatus("expired", code_hash=h, name=row["name"], expires_at=row["expires_at"])
            used = self._uses_count(con, h)
            if used >= row["packs_limit"]:
                return CodeStatus("used_up", code_hash=h, name=row["name"], packs_limit=row["packs_limit"],
                                  expires_at=row["expires_at"])
            return CodeStatus("ok", code_hash=h, name=row["name"], packs_limit=row["packs_limit"],
                              packs_left=row["packs_limit"] - used, expires_at=row["expires_at"])

    # -- spending a pack ------------------------------------------------------
    def claim_pack(self, code_hash: str, file_sha256: str) -> str:
        """'rerun' (free, already counted) or 'new' (this call just spent one). Raises NoPacksLeft/UnknownCode."""
        with self._tx() as con:
            if con.execute("SELECT 1 FROM pack_uses WHERE code_hash=? AND file_sha256=?",
                           (code_hash, file_sha256)).fetchone():
                return "rerun"
            row = self._code_row(con, code_hash)
            if row is None:
                raise UnknownCode(code_hash)
            if self._uses_count(con, code_hash) >= row["packs_limit"]:
                raise NoPacksLeft(code_hash)
            con.execute("INSERT INTO pack_uses(code_hash, file_sha256, created_at) VALUES (?,?,?)",
                       (code_hash, file_sha256, _now()))
            return "new"

    def refund_pack(self, code_hash: str, file_sha256: str) -> None:
        """The audit crashed: give the pack back. Safe to call even if finalize_pack already ran (no-op then,
        since a finished audit should not be refunded - callers only reach this on an exception)."""
        with self._tx() as con:
            con.execute("DELETE FROM pack_uses WHERE code_hash=? AND file_sha256=? AND n_issues IS NULL",
                       (code_hash, file_sha256))

    def finalize_pack(self, code_hash: str, file_sha256: str, rfq_reference: str, n_issues: int) -> None:
        with self._tx() as con:
            con.execute("UPDATE pack_uses SET rfq_reference=?, n_issues=? WHERE code_hash=? AND file_sha256=?",
                       (rfq_reference, n_issues, code_hash, file_sha256))

    # -- admin reads ----------------------------------------------------------
    def list_codes(self) -> list[dict]:
        with self._ro() as con:
            rows = con.execute("SELECT c.*, (SELECT COUNT(*) FROM pack_uses p WHERE p.code_hash=c.code_hash) used "
                               "FROM codes c ORDER BY c.created_at").fetchall()
            return [dict(r) for r in rows]

    def show_code(self, raw_code: str) -> dict | None:
        h = hash_code(normalize(raw_code))
        with self._ro() as con:
            row = self._code_row(con, h)
            if row is None:
                return None
            uses = con.execute("SELECT file_sha256, rfq_reference, n_issues, created_at FROM pack_uses "
                               "WHERE code_hash=? ORDER BY created_at", (h,)).fetchall()
            return {**dict(row), "used": len(uses), "uses": [dict(u) for u in uses]}

    def extend_code(self, raw_code: str, add_days: int = 0, add_packs: int = 0) -> bool:
        """Also reactivates a withdrawn code. Returns False if the code is not known."""
        h = hash_code(normalize(raw_code))
        with self._tx() as con:
            row = self._code_row(con, h)
            if row is None:
                return False
            new_expiry = (date.fromisoformat(row["expires_at"]) + timedelta(days=add_days)).isoformat()
            con.execute("UPDATE codes SET expires_at=?, packs_limit=packs_limit+?, revoked=0 WHERE code_hash=?",
                       (new_expiry, add_packs, h))
            return True

    def revoke_code(self, raw_code: str) -> bool:
        h = hash_code(normalize(raw_code))
        with self._tx() as con:
            row = self._code_row(con, h)
            if row is None:
                return False
            con.execute("UPDATE codes SET revoked=1 WHERE code_hash=?", (h,))
            return True


# --------------------------------------------------------------------------- wrong-code lockout
# Pure functions over a plain dict so they're testable without Streamlit; app.py passes st.session_state,
# which supports the same get/__setitem__/pop interface.

def record_attempt(state, ok: bool) -> None:
    if ok:
        state.pop("pilot_fail_count", None)
        state.pop("pilot_locked_until", None)
        return
    n = state.get("pilot_fail_count", 0) + 1
    if n >= MAX_ATTEMPTS:
        state["pilot_locked_until"] = time.time() + LOCKOUT_SECONDS
        state["pilot_fail_count"] = 0
    else:
        state["pilot_fail_count"] = n


def lockout_remaining(state) -> float:
    until = state.get("pilot_locked_until")
    if not until:
        return 0.0
    remaining = until - time.time()
    if remaining <= 0:
        state.pop("pilot_locked_until", None)
        return 0.0
    return remaining


# --------------------------------------------------------------------------- file retention

def purge_old_files(dirs, max_age_hours: float = 24) -> int:
    """Delete files older than max_age_hours under each directory. Returns how many were removed."""
    cutoff, removed = time.time() - max_age_hours * 3600, 0
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
    return removed


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PILOT_TERMS = (
    "TenderLens is a review aid, not a verdict. Handwritten ticks, dates and stamps are not read yet and are "
    "listed for you to check by eye. Files are kept for at most 24 hours. Running the same file again doesn't "
    "use another pack. Use only documents you are allowed to upload."
)
