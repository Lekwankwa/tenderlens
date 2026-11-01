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
from .facts import (CROP_VERIFY_ENABLED, Facts, combined_first_pass, combined_vision_enabled, inspect_pages, load_transcript, ocr_facts, signature_match,
                    verify_identifiers, verify_yesno, vision_extract, vision_precheck)
from .ingest import (classify_pages, find_spec_sheet, load_pdf, load_templates, page_count_estimate,
                     pdf_page_count)
from .llm import ModelClient, load_env
from .report import render_html, to_json
from .pricing import run_pricing_check
from .requirements import build_requirements

def _tender_text(rfq, spec) -> str | None:
    """The buyer's own text (the RFQ/Tender files and the spec, text layer only - no OCR here), or None when none of it has a
    text layer."""
    import subprocess
    files = [rfq] if isinstance(rfq, (str, Path)) else list(rfq)
    files += [spec] if spec else []
    out = [subprocess.run(["pdftotext", "-layout", str(f), "-"], capture_output=True, text=True).stdout for f in files]
    text = "\n".join(out)
    return text if len(text.strip()) >= 200 else None


def apply_sbd_gate(pages, rfq, spec) -> str:
    """SBD forms are the South African standard bidding documents. A tender that never names them (an NEC3 civil
    engineering contract, say) is not checked against them: every page the classifier gave an SBD type is set back to
    "unknown", so none of the SBD rule loops (SBD 4, 6.1, 6.2, 8, 9, local content, page counts, entry checks) and no
    vision reading of those forms runs. When the tender has no text layer to read, the pack decides: it counts as an SBD
    pack when at least two pages carry an SBD form header of their own. Returns the note for the report, or "" when the
    SBD checks stay on."""
    from .ingest import SBD_TYPES, sbd_header_pages, tender_designates_sbd
    designated = tender_designates_sbd(_tender_text(rfq, spec))
    basis = "the tender text"
    if designated is None:
        designated, basis = sbd_header_pages(pages) >= 2, "the pack (the tender has no readable text)"
    if designated:
        return ""
    skipped = [p for p in pages if p.doc_type in SBD_TYPES]
    for p in skipped:
        p.doc_type = "unknown"
    return (f"SBD form checks were not run: {basis} does not designate the South African SBD forms, so "
            f"{len(skipped)} page(s) that look like SBD forms were left unchecked against them.")


def audit_pack(rfq, spec, pack, blank, transcript=None, work=Path(".cache"), llm=None, local_content="auto",
               vision=None, inspect="forms", passes=2, extra_facts=None, progress=None, pack_stage="final",
               job_id=None, live=None):
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
    live     a live.LiveState, if the caller wants to show progress page by page while the audit runs (the pages and
             their form types once classified, each page's first reading and second looks, and the requirements the
             moment the text step has them). None (the default) changes nothing.
    job_id   None (the default, every caller outside the Cloud Run large-upload path) means no vision
             caching - today's exact behaviour. Given, it lets a vision call already answered before this
             same job was interrupted be skipped on resume instead of re-asked - see facts._ask() and
             vision_cache.py. Harmless to pass even without GCS configured (every lookup just misses).

    Timing: facts.stage_times ends up with seconds spent in "render", "ocr", "text_model" (the RFQ/spec
    reading, including any separate spec file's own render+OCR), "vision_first" and "vision_second" (second
    looks AND the inspect_pages/signature_match checks - see vision_extract's and inspect_pages' own
    docstrings for exactly what each covers). "report" is added by the caller (cli.py's main() / app.py),
    since building the report happens after this function returns. The requirements step (text_model) and
    the pack's own render+OCR are independent of each other and run concurrently in two threads. Vision needs
    only the classified pack pages, so it starts as soon as OCR is done and runs while the requirements step
    finishes; only the rule checks wait for both."""
    llm = llm or ModelClient()
    vision = vision or ModelClient.for_vision(llm)
    timing: dict[str, float] = {}

    req_box: dict = {}

    def build_req():
        t0 = time.time()
        spec_text, spec_name = "", ""
        rfq_files, spec_file = ([rfq] if isinstance(rfq, (str, Path)) else list(rfq)), spec
        if spec_file is None and len(rfq_files) > 1:
            # The web app has one RFQ/Tender upload and passes no separate spec, so a specification sheet
            # uploaded with the rest was never read (it is usually a scan, which plain text extraction sees as
            # empty) and every quantity / item / delivery-address check against it was silently skipped.
            spec_file = find_spec_sheet(rfq_files, work)
            if spec_file is not None:
                rfq_files = [p for p in rfq_files if p != spec_file]
        if spec_file:
            spec_pages = load_pdf(spec_file, work, timing=timing)
            spec_text, spec_name = spec_pages[0].text, spec_file.name
        req_box["req"] = build_requirements(rfq_files, spec_text, spec_name, llm if llm.configured else None,
                                            local_content, work=work)
        timing["text_model"] = timing.get("text_model", 0.0) + (time.time() - t0)
        if live is not None:
            live.set_requirements(req_box["req"])

    req_thread = threading.Thread(target=build_req)
    req_thread.start()
    pages = load_pdf(pack, work, progress=(lambda d, t: progress("ocr", d, t)) if progress else None, timing=timing)

    classify_pages(pages, load_templates(blank))
    gate_note = apply_sbd_gate(pages, rfq, spec)
    if live is not None:
        from .audit import DOC_LABEL
        live.set_pages(pages, lambda dtype: DOC_LABEL.get(dtype, dtype))
    # Vision does not need the requirements (the local-content fields it reads belong to the declaration form, which
    # is read whenever it is in the pack), so it starts the moment OCR is done and the requirements step - often the
    # slowest single stage - finishes alongside it; the rules only need `req` after that. The same endpoint is being
    # used by the text call right now, so the one-call "is vision up at all" precheck is skipped, as it was when the
    # text call had already answered.
    proven = bool(llm.configured and llm.base_url == vision.base_url and llm.model == vision.model)

    def get_req():
        req_thread.join()
        if "req" not in req_box:
            raise RuntimeError("The requirements step stopped before it produced a result.")
        return req_box["req"]

    facts, res, req = _vision_and_audit(get_req, pages, blank, vision, inspect, passes, pack_stage, transcript,
                                        extra_facts, progress, timing, job_id, endpoint_proven=proven, live=live)
    facts.stage_times = timing
    if gate_note:
        res.human_only.append(gate_note)
    return req, res, facts, pages


def _vision_and_audit(req, pages, blank, vision, inspect, passes, pack_stage, transcript, extra_facts, progress,
                      timing, job_id=None, endpoint_proven=False, live=None):
    """The part of audit_pack() after OCR/requirements: read the pack with vision (if configured) and run the
    rule checks. Split out so a free re-run (vision was busy, nothing about the pack or the buyer's
    documents changed) can redo just this part, reusing the already-read req/pages instead of paying for
    OCR and the text model again. `req` is the Requirements, or a function that waits for them and returns them -
    the form readings do not need them, so they can still be in progress while vision runs. Returns
    (facts, result, requirements)."""
    facts = Facts()
    if vision.use_large_pack_profile(len(pages)):
        print(f"large pack ({len(pages)} pages): page images are sent to the model at reduced size", flush=True)
    facts.job_id = job_id  # see audit_pack's own docstring and facts._ask()/vision_cache.py
    ocr_facts(pages, facts)
    if vision.configured:
        facts.vision_log["model"] = vision.model
        if endpoint_proven or vision_precheck(vision, pages, facts):
            t_vision = time.time()
            checks = (lambda d, t: progress("vision_checks", d, t)) if progress else None
            # The page inspection and the signature comparison don't use the form readings, so they run side
            # by side with them, not after. Each writes to its own Facts and timing, folded back below in the
            # order the steps used to run one after another - so the report comes out the same; only the wait
            # is shorter. The vision client caps calls in flight across all three (see ModelClient._slots).
            side: list[tuple[int, Facts, dict, threading.Thread]] = []
            errors: list[BaseException] = []

            def start(order, step):
                f, t = facts.branch(), {}

                def run():
                    try:
                        step(f, t)
                    except BaseException as err:  # noqa: BLE001 - re-raised on the calling thread below
                        errors.append(err)
                th = threading.Thread(target=run, daemon=True)
                th.start()
                side.append((order, f, t, th))

            first_progress = (lambda d, t: progress("vision_first", d, t)) if progress else None
            # a local content declaration that is IN the pack is read and checked whether or not the RFQ said local
            # content applies: its own figures are what the quote total and the SBD 6.2 answers are compared with
            # (every field that only applies "when local content applies" is on the declaration form itself, so
            # whether the RFQ/Tender requires local content changes nothing about what vision reads)
            local = True
            start(3, lambda f, t: run_pricing_check(vision, pages, f, passes=passes, timing=t))  # priced schedules
            if inspect != "off":
                start(2, lambda f, t: signature_match(vision, pages, f, progress=checks, timing=t))
            first = None
            # each page's second looks are asked as soon as that page's first reading is in, not after every page's
            ahead: dict | None = {} if passes >= 2 else None
            if combined_vision_enabled():
                # one call per form page for both the field reading and the inspection questions (see
                # facts.combined_first_pass) - half the page images sent, so less queuing at the provider
                first = combined_first_pass(vision, pages, facts, local_content=local,
                                            inspect=inspect != "off", progress=first_progress, timing=timing,
                                            prefetch=ahead, live=live)
            if inspect != "off":
                start(1, lambda f, t: inspect_pages(vision, pages, f, passes, progress=checks, timing=t,
                                                    first_answers=first, prefetched=ahead))
            vision_extract(vision, pages, facts, local_content=local, verify=passes >= 2,
                           progress=None if first is not None else first_progress, timing=timing,
                           first_answers=first, prefetched=ahead)
            if passes >= 2 and CROP_VERIFY_ENABLED:
                verify_identifiers(vision, pages, facts)
                verify_yesno(vision, pages, facts)
            for _, f, t, th in sorted(side, key=lambda x: x[0]):  # inspection's facts, then the signatures'
                th.join()
                facts.merge(f)
                for k, v in t.items():
                    timing[k] = timing.get(k, 0.0) + v
            if errors:
                raise errors[0]
            timing["vision_total"] = time.time() - t_vision
    if vision.configured and (facts.vision_log.get("stopped_early") or facts.vision_log.get("failed", 0) > 0):
        facts.vision_complete = False
    tinfo = {"unmatched": {}}
    if transcript:
        tinfo = load_transcript(transcript, facts, pages)
    if extra_facts:
        extra_facts(facts, pages)
    req = req() if callable(req) else req
    res = Auditor(req, pages, facts, blank, tinfo["unmatched"], pack_stage=pack_stage).run()
    res.ocr_variants.extend(facts.misread_variants)
    return facts, res, req


def rerun_vision(req, pages, blank, vision, inspect="forms", passes=2, pack_stage="final", transcript=None,
                 extra_facts=None, progress=None, job_id=None):
    """Free re-run of an incomplete Complete check: vision was busy last time, nothing about the buyer's
    documents or the pack changed, so OCR and the text model (requirements) are not repeated - pages already
    carries their OCR text, req is reused as-is. Returns (res, facts), timed the same way as audit_pack's own
    vision_first/vision_second (no render/ocr/text_model entries, since none of that runs here).
    job_id: see audit_pack's own docstring - with one set, pages vision already answered before this aren't
    re-asked (facts._ask()/vision_cache.py), which is what makes this genuinely free, not just pilot-credit-free."""
    timing: dict[str, float] = {}
    facts, res, _ = _vision_and_audit(req, pages, blank, vision, inspect, passes, pack_stage, transcript,
                                      extra_facts, progress, timing, job_id)
    facts.stage_times = timing
    return res, facts


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
    text = ModelClient()
    vision = ModelClient.for_vision(text)
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
    r.add_argument("--pack-stage", choices=["draft", "final"], default="final",
                   help="draft = initials aren't expected yet, a missing one isn't flagged. final = they are, a "
                        "missing one is an issue.")
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
    u = sub.add_parser("ui", help="start Ollama if needed, fetch the configured local model(s), open the web interface")
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
        vision = ModelClient.for_vision(ModelClient())
        if not vision.configured:
            raise SystemExit("No model configured. Set FIREWORKS_API_KEY (see README).")
        print(eval_vision(vision, a.pack, a.blank, a.transcript, a.work, a.out, a.passes))
        return

    llm = ModelClient()
    fast = a.mode == "fast" or a.no_vision
    check_mode = "fast" if fast else "complete"
    vision = ModelClient(base_url="", model="") if fast else ModelClient.for_vision(llm)
    if not fast and not vision.configured:
        print("No vision model is connected, so Complete check can't read handwriting, ticks or stamps. "
              "Add --mode fast, or set FIREWORKS_API_KEY (see README).")
    llm_mode = ("Muse Spark (" + llm.model + ") cross-checked with rules" if llm.configured
               else "rules only: no model configured (set FIREWORKS_API_KEY)")
    print(f"{check_mode.capitalize()} check. " + page_count_estimate(pdf_page_count(a.pack), check_mode))

    STAGE_LABEL = {"ocr": "OCR", "vision_first": "vision first readings", "vision_checks": "vision checks"}

    def cli_progress(stage, done, total):
        print(f"\r  {STAGE_LABEL.get(stage, stage)}: {done} of {total}" + (" " * 10), end="", flush=True)

    a.out.mkdir(parents=True, exist_ok=True)
    req, res, facts, pages = audit_pack(a.rfq, a.spec, a.pack, a.blank, a.transcript, a.work, llm,
                                        a.local_content, vision=vision, inspect=a.vision_inspect, passes=a.passes,
                                        progress=cli_progress, pack_stage=a.pack_stage)
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
    if check_mode == "complete" and not facts.vision_complete:
        failed_pages = sorted(facts.vision_log.get("failed_pages") or [])
        print("INCOMPLETE: handwriting, ticks and stamps were not fully read. Pages that failed: "
             f"{failed_pages or 'not recorded'}. Run it again.")
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
