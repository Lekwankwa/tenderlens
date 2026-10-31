"""Run the CE packs through the real engine, one grade at a time, with the answer key withheld.

    python tools/ce_eval.py --grades 4,1,8 [--kind clean|tampered|both] [--tag dpi100] [--dry-run]

Only grades 4, 1 and 8 can be run from here (ALLOWED_GRADES); there is no "all grades" switch. Every pack is sent to the
model at 100 DPI, JPEG quality 85, and a run aborts if that profile is not active. Spend is tracked in US$ at the plan
rates below, summed over EVERY run already saved under runs/ (not just this session), and the loop stops before a pack whose
projected cost would take the total past HARD_STOP_USD; the stop is written to budget_warnings.json.

Inputs come from evaluation/ce_baseline/manifest.json (written by ce_discover.py). Only the roles tender, clean_pack
and tampered_pack are ever selected here (see select_inputs); this program never opens an answer key or the
requirements CSV, so nothing in the engine's worker context can see them. Scoring is a separate step (ce_score.py).

Per run it saves report.json (the engine's own JSON) and run.json (seconds, stage times, calls, tokens, retries,
errors, failed pages, peak memory, provider and model; there is one provider, Fireworks, and no fallback lane).
A finished run is skipped on re-run unless --force. Nothing under ~/Downloads is modified.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens.cli import audit_pack  # noqa: E402
from tenderlens.ingest import pdf_page_count  # noqa: E402
from tenderlens.llm import ModelClient, load_env  # noqa: E402
from tenderlens.report import to_json  # noqa: E402

ALLOWED_ROLES = ("tender", "clean_pack", "tampered_pack")
ALLOWED_GRADES = (4, 1, 8)          # the smaller test set, in this order; widen deliberately, here, not by a flag
# Fireworks serverless pricing for muse-spark-open, US$ per 1,000,000 tokens
INPUT_TOKEN_PRICE = 1.25
CACHED_INPUT_TOKEN_PRICE = 0.15
OUTPUT_TOKEN_PRICE = 4.25
HARD_STOP_USD = 78.00               # stop here, leaving a buffer under the 80 threshold
# projection per page: the grade 2 pilot at 100 DPI measured about 2.4k prompt and 0.4k completion tokens per page;
# these are deliberately higher
PROJECT_PROMPT_PER_PAGE = 3_000
PROJECT_COMPLETION_PER_PAGE = 500
IMAGE_PROFILE = {"TENDERLENS_LARGE_PACK_PROFILE": "1", "TENDERLENS_LARGE_PACK_DPI": "100",
                 "TENDERLENS_LARGE_PACK_QUALITY": "85", "TENDERLENS_BATCH_OVER": "50", "TENDERLENS_BATCH_PAGES": "25"}


def select_inputs(scenario: dict, root: Path) -> dict:
    """{"tender": [paths in name order], "clean": path, "tampered": path} - and nothing else. The answer key and the
    requirements CSV are in the manifest too, but are never selected."""
    by_role: dict[str, list[Path]] = {}
    for f in scenario["files"]:
        if f["role"] in ALLOWED_ROLES:
            by_role.setdefault(f["role"], []).append(root / f["path"])
    return {"tender": sorted(by_role.get("tender", []), key=lambda p: p.name.lower()),
            "clean": by_role["clean_pack"][0], "tampered": by_role["tampered_pack"][0]}


def projected_usd(pages: int) -> float:
    return pages * (PROJECT_PROMPT_PER_PAGE * INPUT_TOKEN_PRICE + PROJECT_COMPLETION_PER_PAGE * OUTPUT_TOKEN_PRICE) / 1e6


def spend_usd(info: dict) -> float:
    """US$ for one run: uncached prompt tokens, cached prompt tokens and completion tokens at their own rates. A run saved
    before cached tokens were recorded has none, so it is priced as if nothing was cached (an upper bound)."""
    cached = min(info.get("cached_tokens", 0), info.get("prompt_tokens", 0))
    return ((info.get("prompt_tokens", 0) - cached) * INPUT_TOKEN_PRICE + cached * CACHED_INPUT_TOKEN_PRICE
            + info.get("completion_tokens", 0) * OUTPUT_TOKEN_PRICE) / 1e6


def spent_so_far(out: Path) -> float:
    """US$ already spent by every run saved under runs/ (any grade, any tag), from the token counts in each run.json."""
    total = 0.0
    for pattern in ("runs/*/*/run.json", "runs_failed/*/run.json"):  # failed runs spent real money too
        for f in out.glob(pattern):
            total += spend_usd(json.loads(f.read_text()))
    return total


def warn(out: Path, message: str, **detail) -> None:
    """Append a structured budget warning to budget_warnings.json (the scorer copies these into the report)."""
    path = out / "budget_warnings.json"
    items = json.loads(path.read_text()) if path.exists() else []
    items.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message, **detail})
    path.write_text(json.dumps(items, indent=1))
    print("WARNING:", message, flush=True)


def usage(llm: ModelClient, vision: ModelClient) -> dict:
    return {"text_calls": llm.calls, "vision_calls": vision.calls,
            "prompt_tokens": llm.prompt_tokens + vision.prompt_tokens,
            "cached_tokens": llm.cached_tokens + vision.cached_tokens,
            "completion_tokens": llm.completion_tokens + vision.completion_tokens,
            "retries": llm.retries + vision.retries, "errors": (llm.errors + vision.errors)[:6],
            "latency": {"text": llm.call_summary(), "vision": vision.call_summary()}}


# Findings of kinds that are known to misfire on documents that are not SBD forms. They are labelled, never removed:
# every finding stays in report.json, and flags.json lists these so they can be read together.
ANOMALY_CLASSES = (
    ("initials", r"^Initials missing"), ("pages_found", r"\d+ of \d+ pages found|pages found, the standard form"),
    ("page_cut_off", r"^Page cut off"), ("missing_document", r"^Missing:"), ("unsigned", r"may be unsigned|no official stamp|"
     r"no commissioner|Could not locate the signature"), ("stray_mark", r"^(Unexplained|Small) mark|Handwritten note"),
    ("cross_document_mismatch", r"differs between documents"), ("rotated", r"^Page rotated"), ("unresolved_area", r"^Unresolved exception area"))


def flag_report(report: dict) -> dict:
    """Every finding of the run grouped by anomaly class. Nothing is suppressed or re-tiered."""
    import re
    groups: dict[str, list] = {}
    for f in report.get("findings", []):
        cls = next((c for c, rx in ANOMALY_CLASSES if re.search(rx, f.get("title", ""))), "other")
        groups.setdefault(cls, []).append({"title": f.get("title"), "tier": f.get("tier"), "severity": f.get("severity"),
                                           "pages": sorted({e.get("page") for e in f.get("evidence", [])
                                                            if isinstance(e.get("page"), int)})})
    return {"total_findings": len(report.get("findings", [])), "by_class": groups}


def run_one(tender: list[Path], pack: Path, out: Path, work: Path, blank: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    llm = ModelClient()
    vision = ModelClient.for_vision(llm)
    t0 = time.time()
    if not vision.use_large_pack_profile(pdf_page_count(pack)) or round(vision.image_scale, 3) != round(100 / 150, 3) \
            or vision.jpeg_quality != 85:
        raise SystemExit(f"refusing to run {pack.name}: the 100 DPI / quality 85 image profile is not active")
    status, err = "ok", ""
    try:
        req, res, facts, pages = audit_pack(tender, None, pack, blank, None, work, llm, "auto", vision=vision)
        (out / "report.json").write_text(to_json(req, res))
        (out / "flags.json").write_text(json.dumps(flag_report(json.loads(to_json(req, res))), indent=1))
        stages, complete = dict(facts.stage_times), facts.vision_complete
        failed_pages = sorted(facts.vision_log.get("failed_pages") or [])
        slice_errors = list(facts.vision_log.get("slice_errors") or [])
        npages, profile = len(pages), vision.image_scale < 1
    except Exception as e:  # noqa: BLE001 - a failed pack is a result to record, not a reason to stop the run
        status, err = "failed", f"{type(e).__name__}: {str(e)[:300]}"
        stages, complete, failed_pages, npages, profile, slice_errors = {}, False, [], 0, False, []
    info = {"pack": pack.name, "status": status, "error": err, "seconds": round(time.time() - t0, 1),
            "pages": npages, "vision_complete": complete, "failed_pages": failed_pages, "stages": stages,
            "slice_errors": slice_errors, "timeouts": llm.timeouts + vision.timeouts,
            "large_pack_profile": profile, "image_dpi": round(150 * vision.image_scale),
            "jpeg_quality": vision.jpeg_quality, "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                                                / (1 << 20), 0),
            "provider": urlparse(llm.base_url).hostname, "model": llm.model, "fallback_fired": False,
            **usage(llm, vision)}
    (out / "run.json").write_text(json.dumps(info, indent=1))
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--grades", default=",".join(map(str, ALLOWED_GRADES)),
                    help=f"subset of {list(ALLOWED_GRADES)}, in the order to run (default: all three)")
    ap.add_argument("--kind", choices=["clean", "tampered", "both"], default="both")
    ap.add_argument("--out", type=Path, default=Path("evaluation/ce_baseline"))
    ap.add_argument("--blank", type=Path, default=Path("samples/blank"))
    ap.add_argument("--tag", default="dpi100", help="keeps this run's results and OCR work area apart")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, projections and budget; call nothing")
    a = ap.parse_args()
    grades = [int(x) for x in a.grades.split(",") if x.strip()]
    bad = [g for g in grades if g not in ALLOWED_GRADES]
    if bad:
        ap.error(f"grade(s) {bad} are not enabled: only {list(ALLOWED_GRADES)} may be run from this tool")
    load_env()
    for k, v in IMAGE_PROFILE.items():  # the image profile is fixed for these runs, whatever the shell has set
        os.environ[k] = v
    manifest = json.loads((a.out / "manifest.json").read_text())
    root = Path(manifest["root"])
    by_grade = {s["grade"]: s for s in manifest["scenarios"]}
    for g in grades:
        sc = by_grade[g]
        inp = select_inputs(sc, root)
        for kind in (["clean", "tampered"] if a.kind == "both" else [a.kind]):
            folder = sc["folder"].replace(" ", "_") + f"__{a.tag}"
            out = a.out / "runs" / folder / kind
            pack = inp[kind]
            if (out / "run.json").exists() and not a.force:
                print(f"grade {g} {kind}: already run, skipping (--force to repeat)")
                continue
            npages = next(f["pages"] for f in sc["files"] if f["path"].endswith(pack.name))
            spent, proj = spent_so_far(a.out), projected_usd(npages)
            print(f"grade {g} {kind}: {pack.name} {npages} pages; projected ${proj:.2f}; spent so far ${spent:.2f} "
                  f"of the ${HARD_STOP_USD:.2f} stop", flush=True)
            if spent >= HARD_STOP_USD or spent + proj > HARD_STOP_USD:
                warn(a.out, "stopping before the model calls: the next pack would take spending past the hard stop",
                     grade=g, kind=kind, pack=pack.name, pages=npages, spent_usd=round(spent, 2),
                     projected_usd=round(proj, 2), hard_stop_usd=HARD_STOP_USD)
                return 1
            if a.dry_run:
                continue
            info = run_one(inp["tender"], pack, out, a.out / ".work" / folder, a.blank)
            print(f"  {info['status']} in {info['seconds']}s at {info['image_dpi']} DPI q{info['jpeg_quality']}, "
                  f"{info['vision_calls']} vision + {info['text_calls']} text calls, {info['prompt_tokens']:,}+"
                  f"{info['completion_tokens']:,} tokens = ${spend_usd(info):.2f}, peak {info['peak_rss_mb']:.0f} MB"
                  + (f", ERROR {info['error']}" if info["error"] else "") + f"; total spent ${spent_so_far(a.out):.2f}",
                  flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
