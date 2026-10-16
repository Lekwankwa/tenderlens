"""python -m tenderlens run --rfq X.pdf [Y.pdf ...] --pack Z.pdf --blank samples/blank --out out/   (--spec is optional)
python -m tenderlens ui | check-llm | eval-vision | pilot"""
from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import date
from pathlib import Path

from .audit import Auditor
from .facts import (CROP_VERIFY_ENABLED, Facts, inspect_pages, load_transcript, ocr_facts, signature_match,
                    verify_identifiers, verify_yesno, vision_extract, vision_precheck)
from .ingest import classify_pages, load_pdf, load_templates, page_count_estimate, pdf_page_count
from .llm import LlamaClient, load_env
from .report import render_html, to_json
from .requirements import build_requirements

def audit_pack(rfq, spec, pack, blank, transcript=None, work=Path(".cache"), llm=None, local_content="auto",
               vision=None, inspect="forms", passes=2, extra_facts=None, progress=None):
    """The whole pipeline, returning (requirements, audit result, facts, pages). Used by the CLI, the app and the tests.

    rfq      the buyer's RFQ, as one path or a list: the cover email/letter plus any attachments it came
             with (annexures, a BOQ, blank SBD templates sent separately). Their text is concatenated in
             the order given before anything is extracted from it, so a requirement stated in any one of
             them is found regardless of how the buyer split the RFQ into files.
    spec     the specification sheet, if the buyer issued one separately from the RFQ - optional; when it is
             None, requirements are read from the RFQ alone (some RFQs state the spec inline, or need none)
    llm      the text model (reads the buyer's documents); vision  the model that reads page images. When no
             vision model is set the text model is used; when neither is configured the handwriting comes from
             the transcript, or is reported as not read.
    inspect  "forms" = look for crossed-out text, notes, rotation etc. on the handwritten forms, "off" = skip
    passes   2 = a vision 'yes' needs a second, differently worded look to count as an issue
    progress, if given, is called as progress(stage, done, total) from whichever stage is currently running
             ("ocr", "vision_first", "vision_second"/"vision_checks") - for a live "page N of M" indicator.

    Timing: facts.stage_times ends up with seconds spent in "render", "ocr", "text_model" (the RFQ/spec
    reading, including any separate spec file's own render+OCR), "vision_first" and "vision_second" (second
    looks AND the inspect_pages/signature_match checks - see vision_extract's and inspect_pages' own
    docstrings for exactly what each covers). "report" is added by the caller (cli.py's main() / app.py),
    since building the report happens after this function returns. The requirements step (text_model) and
    the pack's own render+OCR are independent of each other and run concurrently in two threads; nothing
    past that point can start until both are done (vision needs the classified pack pages AND
    req.local_content_required), so there is no further overlap opportunity in this pipeline."""
    llm = llm or LlamaClient()
    vision = vision or LlamaClient.for_vision(llm)
    timing: dict[str, float] = {}

    req_box: dict = {}

    def build_req():
        t0 = time.time()
        spec_text, spec_name = "", ""
        if spec:
            spec_pages = load_pdf(spec, work, timing=timing)
            spec_text, spec_name = spec_pages[0].text, spec.name
        req_box["req"] = build_requirements(rfq, spec_text, spec_name, llm if llm.configured else None, local_content)
        timing["text_model"] = timing.get("text_model", 0.0) + (time.time() - t0)

    req_thread = threading.Thread(target=build_req)
    req_thread.start()
    pages = load_pdf(pack, work, progress=(lambda d, t: progress("ocr", d, t)) if progress else None, timing=timing)
    req_thread.join()
    req = req_box["req"]

    classify_pages(pages, load_templates(blank))
    facts = Facts()
    ocr_facts(pages, facts)
    if vision.configured:
        facts.vision_log["model"] = vision.model
        if vision_precheck(vision, pages, facts):
            vision_extract(vision, pages, facts, local_content=req.local_content_required, verify=passes >= 2,
                           progress=(lambda d, t: progress("vision_first", d, t)) if progress else None,
                           timing=timing)
            if passes >= 2 and CROP_VERIFY_ENABLED:
                verify_identifiers(vision, pages, facts)
                verify_yesno(vision, pages, facts)
            if inspect != "off":
                inspect_pages(vision, pages, facts, passes,
                              progress=(lambda d, t: progress("vision_checks", d, t)) if progress else None,
                              timing=timing)
                signature_match(vision, pages, facts,
                                progress=(lambda d, t: progress("vision_checks", d, t)) if progress else None,
                                timing=timing)
    tinfo = {"unmatched": {}}
    if transcript:
        tinfo = load_transcript(transcript, facts, pages)
    if extra_facts:
        extra_facts(facts, pages)
    res = Auditor(req, pages, facts, blank, tinfo["unmatched"]).run()
    res.ocr_variants.extend(facts.misread_variants)
    facts.stage_times = timing
    return req, res, facts, pages


def pilot_cmd(a):
    """Create and manage pilot access codes. The code is shown only at creation time; we never store it."""
    from . import pilot
    store = pilot.PilotStore()
    if a.pilot_cmd == "create":
        code = store.create_code(a.name, a.email or "", a.packs, a.days)
        print(f"Code (shown once, not stored - write it down): {code}")
        print(f"For: {a.name}" + (f" <{a.email}>" if a.email else "") + f", {a.packs} pack(s), {a.days} day(s).")
    elif a.pilot_cmd == "list":
        rows = store.list_codes()
        if not rows:
            print("No codes yet.")
        for r in rows:
            status = "withdrawn" if r["revoked"] else ("expired" if date.today().isoformat() > r["expires_at"]
                                                        else "active")
            print(f"{r['name']:24s} {r['used']}/{r['packs_limit']} packs  until {r['expires_at']}  {status}"
                 + (f"  <{r['email']}>" if r["email"] else ""))
    elif a.pilot_cmd == "show":
        row = store.show_code(a.code)
        if row is None:
            print("Not a known code.")
            return
        status = "withdrawn" if row["revoked"] else ("expired" if date.today().isoformat() > row["expires_at"]
                                                      else "active")
        print(f"{row['name']}" + (f" <{row['email']}>" if row["email"] else ""))
        print(f"{status}, {row['used']}/{row['packs_limit']} packs used, valid until {row['expires_at']}, "
              f"created {row['created_at']}")
        for u in row["uses"]:
            print(f"  {u['created_at']}  file {u['file_sha256'][:12]}...  RFQ {u['rfq_reference'] or '?'}  "
                 f"{u['n_issues'] if u['n_issues'] is not None else '?'} issue(s)")
    elif a.pilot_cmd == "extend":
        ok = store.extend_code(a.code, a.days, a.packs)
        print("Extended (and reactivated if it was withdrawn)." if ok else "Not a known code.")
    elif a.pilot_cmd == "revoke":
        ok = store.revoke_code(a.code)
        print("Withdrawn." if ok else "Not a known code.")


def check_llm(a):
    """Tell the user, in plain words, whether the tools, endpoints and models work. Run this before anything else."""
    from .bootstrap import system_check
    print("Tools and endpoints")
    bad = 0
    for item, ok, detail in system_check():
        print(f"  {'ok     ' if ok else 'MISSING'} {item}" + (f"  ({detail})" if detail and not ok else ""))
        bad += not ok
    print(f"\n{'Everything needed is in place.' if not bad else f'{bad} problem(s) above.'}\n")
    text = LlamaClient()
    vision = LlamaClient.for_vision(text)
    if vision.configured:
        pages = load_pdf(a.pack, a.work)
        print(f"reading page 1 of {a.pack.name} with {vision.model} (this can take a minute on a laptop)...")
        try:
            got = vision.complete_json("Reply with JSON only.", 'Describe this page in one short sentence. Return '
                                       '{"description": "..."}', images=[pages[0].image_path])
            print("vision saw:", got.get("description"))
        except Exception as err:  # noqa: BLE001
            print("page read FAILED:", type(err).__name__, str(err)[:200])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tenderlens")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="audit a response pack")
    r.add_argument("--rfq", required=True, type=Path, nargs="+", metavar="PDF",
                   help="the buyer's RFQ: the cover email/letter plus any attachments it came with (up to 10 "
                        "PDFs), in the order they should be read")
    r.add_argument("--spec", type=Path,
                   help="specification sheet PDF (scan ok), if the buyer issued one separately from the RFQ. Optional.")
    r.add_argument("--pack", required=True, type=Path, help="response pack PDF (scan ok)")
    r.add_argument("--blank", required=True, type=Path, help="folder of blank SBD forms exported as .txt")
    r.add_argument("--transcript", type=Path, help="manual transcription JSON for handwriting (when no vision model)")
    r.add_argument("--out", type=Path, default=Path("out"))
    r.add_argument("--work", type=Path, default=Path(".cache"))
    r.add_argument("--bidder", default="Bidder")
    r.add_argument("--local-content", choices=["auto", "yes", "no"], default="auto",
                   help="does local content apply to this bid? auto = decide from the buyer's documents")
    r.add_argument("--vision-inspect", choices=["forms", "off"], default="forms",
                   help="with a vision model: also look for crossed-out text, notes, rotated pages (one more call per page)")
    r.add_argument("--passes", type=int, choices=[1, 2], default=2,
                   help="2 = every suspicious vision reading gets a second, differently worded look before it can be an issue")
    r.add_argument("--no-redact", action="store_true", help="DO NOT use for anything you will share")
    r.add_argument("--strict-redact", action="store_true", help="also mask registration and CSD numbers")
    r.add_argument("--no-vision", action="store_true",
                   help="text and rules only, even if a vision endpoint is configured (for a side-by-side comparison)")
    r.add_argument("--mode", choices=["fast", "complete"], default="complete",
                   help="fast = text and rules only, no vision calls. complete = text and vision, both vision "
                        "options always on (default). Same effect as --no-vision when set to fast.")
    c = sub.add_parser("check-llm", help="check tools, endpoints and models, and read one page")
    c.add_argument("--pack", type=Path, default=Path("samples/response/pack.pdf"))
    c.add_argument("--work", type=Path, default=Path(".cache"))
    u = sub.add_parser("ui", help="start Ollama if needed, fetch the configured Llama model(s), open the web interface")
    u.add_argument("--port", type=int, default=8501)
    e = sub.add_parser("eval-vision", help="score the vision model against the winning pack's transcript")
    e.add_argument("--pack", type=Path, default=Path("samples/response/pack.pdf"))
    e.add_argument("--transcript", type=Path, default=Path("samples/response/pack.transcript.json"))
    e.add_argument("--blank", type=Path, default=Path("samples/blank"))
    e.add_argument("--out", type=Path, default=Path("out"))
    e.add_argument("--work", type=Path, default=Path(".cache"))
    e.add_argument("--passes", type=int, choices=[1, 2], default=2)
    p = sub.add_parser("pilot", help="create and manage pilot access codes")
    pp = p.add_subparsers(dest="pilot_cmd", required=True)
    pc = pp.add_parser("create", help="create a code (shown once, not stored)")
    pc.add_argument("--name", required=True)
    pc.add_argument("--email", default="")
    pc.add_argument("--packs", type=int, default=3)
    pc.add_argument("--days", type=int, default=21)
    pp.add_parser("list", help="list every code, packs used and status")
    ps = pp.add_parser("show", help="show one code's details and pack-by-pack usage")
    ps.add_argument("code")
    pe = pp.add_parser("extend", help="add days and/or packs; also reactivates a withdrawn code")
    pe.add_argument("code")
    pe.add_argument("--days", type=int, default=0)
    pe.add_argument("--packs", type=int, default=0)
    pr = pp.add_parser("revoke", help="withdraw a code")
    pr.add_argument("code")
    a = ap.parse_args(argv)
    if a.cmd == "run" and len(a.rfq) > 10:
        ap.error(f"--rfq takes at most 10 PDFs, got {len(a.rfq)}")
    load_env()
    if a.cmd == "ui":
        from .bootstrap import launch_ui
        return launch_ui(a.port)
    if a.cmd == "check-llm":
        return check_llm(a)
    if a.cmd == "pilot":
        return pilot_cmd(a)
    if a.cmd == "eval-vision":
        from .evalvision import eval_vision
        vision = LlamaClient.for_vision(LlamaClient())
        if not vision.configured:
            raise SystemExit("No vision model configured. Set TENDERLENS_VISION_BASE_URL / _MODEL (see OLLAMA.md).")
        print(eval_vision(vision, a.pack, a.blank, a.transcript, a.work, a.out, a.passes))
        return

    llm = LlamaClient()
    fast = a.mode == "fast" or a.no_vision
    check_mode = "fast" if fast else "complete"
    vision = LlamaClient(base_url="", model="") if fast else LlamaClient.for_vision(llm)
    if not fast and not vision.configured:
        print("No vision model is connected, so Complete check can't read handwriting, ticks or stamps. "
              "Add --mode fast, or set TENDERLENS_VISION_BASE_URL / _MODEL (see OLLAMA.md).")
    llm_mode = ("Llama (" + llm.model + ") cross-checked with rules" if llm.configured
               else "rules only: no Llama endpoint configured (set TENDERLENS_LLM_BASE_URL / _MODEL)")
    print(f"{check_mode.capitalize()} check. " + page_count_estimate(pdf_page_count(a.pack), check_mode))

    STAGE_LABEL = {"ocr": "OCR", "vision_first": "vision first readings", "vision_checks": "vision checks"}

    def cli_progress(stage, done, total):
        print(f"\r  {STAGE_LABEL.get(stage, stage)}: {done} of {total}" + (" " * 10), end="", flush=True)

    a.out.mkdir(parents=True, exist_ok=True)
    req, res, facts, pages = audit_pack(a.rfq, a.spec, a.pack, a.blank, a.transcript, a.work, llm,
                                        a.local_content, vision=vision, inspect=a.vision_inspect, passes=a.passes,
                                        progress=cli_progress)
    print()  # end the last \r-updated progress line
    t_report = time.time()
    html = render_html(req, res, facts, pages, a.bidder, req.reference, llm_mode,
                       redact=not a.no_redact, strict=a.strict_redact, check_mode=check_mode)
    (a.out / "report.html").write_text(html)
    (a.out / "report.json").write_text(to_json(req, res))
    facts.stage_times["report"] = time.time() - t_report
    n_issue = sum(1 for f in res.findings if f.tier == "issue")
    n_obs = len(res.findings) - n_issue
    print(f"{n_issue} issues, {n_obs} observations; {len(res.passed)} checks passed -> {a.out / 'report.html'}")
    for label, c in (("text", llm), ("vision", vision)):
        if not c.calls:
            continue
        cost = f", est. ${c.estimated_cost:.4f}" if c.estimated_cost is not None else ""
        print(f"  {label}: {c.model}, {c.calls} call(s), {c.prompt_tokens:,} prompt tokens, "
             f"{c.completion_tokens:,} completion tokens{cost}")
    vl = facts.vision_log
    if vl.get("stopped_early"):
        print(f"  vision stopped early: {vl['stopped_early']}")
    STAGE_NAMES = {"render": "page rendering", "ocr": "OCR", "text_model": "text model (requirements)",
                  "vision_first": "vision first readings", "vision_second": "vision second looks and checks",
                  "report": "report"}
    times = facts.stage_times
    if times:
        total = sum(times.values())
        print("  timing (" + f"{total:.1f}s total, stages that ran concurrently overlap in wall-clock time" + "):")
        for key in ("render", "ocr", "text_model", "vision_first", "vision_second", "report"):
            if key in times:
                print(f"    {STAGE_NAMES[key]}: {times[key]:.1f}s")


if __name__ == "__main__":
    main()
