"""One append-only record per audit run, for debugging reliability (retries, incomplete runs, credit
spend) - never document contents or personal data: no filenames, bidder names, RFQ references, page text
or identifiers, just counts and booleans.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LOG_PATH = Path(os.environ.get("TENDERLENS_CHECK_LOG", ".cache/check_log.jsonl"))


def record(*, check_mode: str, complete: bool, pages_failed: list[int], vision_calls: int, vision_retries: int,
          text_retries: int, credit_used: bool, free_rerun: bool = False, path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": round(time.time()),
        "check_mode": check_mode,
        "complete": complete,
        "pages_failed": sorted(pages_failed),
        "vision_calls": vision_calls,
        "vision_retries": vision_retries,
        "text_retries": text_retries,
        "credit_used": credit_used,
        "free_rerun": free_rerun,
    }
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
