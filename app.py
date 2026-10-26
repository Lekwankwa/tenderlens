"""TenderLens - Streamlit interface.   Run:  streamlit run app.py

Upload the buyer's RFQ documents (and specification, if there is one), then the bidder's response pack, and press Audit.
Everything on screen comes from tenderlens.cli.audit_pack, the same pipeline as the command line.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

import streamlit as st
from pypdf import PdfWriter
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from tenderlens import check_log, pilot, privacy
from tenderlens.audit import DOC_LABEL
from tenderlens.bootstrap import is_local, remote_warning, system_check
from tenderlens.cli import audit_pack, rerun_vision
from tenderlens.ingest import page_count_estimate, pdf_page_count
from tenderlens.llm import BUSY_MESSAGE, LlamaClient, is_rate_limited, load_env
from tenderlens.report import DISCLAIMER_LONG, DISCLAIMER_SHORT, NO_ISSUES_NOTE, SEV, SRC_LABEL, Renderer, render_html, to_json

load_env()
try:  # hosted (e.g. Streamlit Community Cloud): its Secrets become env vars our config already reads.
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass  # no secrets.toml locally - fine, that's what .env / the environment is for
PILOT = pilot.pilot_enabled()  # TENDERLENS_PILOT=1. Off: the app behaves exactly as it always has.
# Human-readable name for whichever MODEL_PROVIDER is active - never the key, just the label. Falls back to
# the raw provider string for an unrecognised value, same as llm.provider_name() does.
PROVIDER_LABEL = {"meta": "Meta's Model API", "openrouter": "OpenRouter"}

ROOT = Path(__file__).resolve().parent
WORK = ROOT / ".cache"
BLANK = ROOT / "samples" / "blank"

st.set_page_config(page_title="TenderLens", page_icon=None, layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
#MainMenu, footer {visibility:hidden}
/* the floating "Hosted with Streamlit" badge Community Cloud injects, bottom right */
[class*="viewerBadge"], a[href^="https://streamlit.io/cloud"] {display:none !important}
.block-container {padding-top:2.2rem; max-width:1180px}
.tl-brand {font-size:1.7rem; font-weight:700; letter-spacing:-.02em; margin:0}
.tl-sub {color:var(--text-color); opacity:.65; margin:.1rem 0 1.1rem}
.tl-banner {border-radius:14px; padding:18px 22px; margin:.4rem 0 1rem; border:1px solid rgba(128,128,128,.25);
            border-left-width:8px}
.tl-banner b {font-size:1.35rem}
.tl-banner span {opacity:.75}
.tl-ok {border-left-color:#2e7d32; background:rgba(46,125,50,.08)}
.tl-bad {border-left-color:#b3261e; background:rgba(179,38,30,.07)}
.tl-chip {display:inline-block; font-size:.72rem; font-weight:600; color:#fff; border-radius:99px; padding:2px 10px;
          margin-right:8px; vertical-align:middle}
.tl-title {font-weight:600; font-size:1.05rem; vertical-align:middle}
.tl-k {font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; opacity:.6; margin:.7rem 0 .1rem}
.tl-tag {font-size:.7rem; border:1px solid rgba(128,128,128,.4); border-radius:4px; padding:0 5px; opacity:.8}
.tl-step {opacity:.6; font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; margin:.9rem 0 .2rem}
div[data-testid="stMetric"] {background:rgba(128,128,128,.07); border:1px solid rgba(128,128,128,.18);
                              border-radius:12px; padding:10px 14px}
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------------- helpers

def save_upload(up, name: str, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(up.getvalue())
    return p


@st.cache_data(show_spinner=False)
def _cached_page_count(data: bytes) -> int:
    """pdf_page_count() shells out to pdfinfo, so without caching this re-saved and re-ran it for every
    uploaded pack file on every single rerun of the page (any widget change, not just a new upload) - the
    page-count caption doesn't need that work redone when the same files are still attached."""
    p = WORK / "uploads" / f"pagecount-{hashlib.sha1(data).hexdigest()[:12]}.pdf"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return pdf_page_count(p)


def merge_uploads(ups: list, name: str, folder: Path) -> Path:
    """One or more uploaded PDFs, concatenated in the order given into a single file (so a pack split
    across several uploads still reads as one continuous set of pack pages)."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    if len(ups) == 1:
        p.write_bytes(ups[0].getvalue())
        return p
    writer = PdfWriter()
    for up in ups:
        writer.append(io.BytesIO(up.getvalue()))
    with open(p, "wb") as f:
        writer.write(f)
    return p


def b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_system_check():
    """system_check() pings the real text/vision endpoints - without caching this ran on every single
    Streamlit rerun (every widget interaction, every viewer, plus the hourly keep-alive load), which adds
    up to real, avoidable traffic against a shared model endpoint. Re-checked at most once every 2 minutes;
    `python -m tenderlens check-llm` (which calls system_check() directly, not this) always checks live."""
    return system_check()


def chip(label: str, color: str) -> str:
    return f"<span class='tl-chip' style='background:{color}'>{label}</span>"


# ----------------------------------------------------------------------------------- sidebar

llm = LlamaClient()
vision = LlamaClient.for_vision(llm)
with st.sidebar:
    st.markdown("<p class='tl-brand'>TenderLens</p><p class='tl-sub'>Audit an RFQ response before you submit it</p>",
                unsafe_allow_html=True)
    st.caption(DISCLAIMER_SHORT)

    st.markdown("<div class='tl-step'>1 &middot; What the buyer sent</div>", unsafe_allow_html=True)
    _max_upload_mb = os.environ.get("TENDERLENS_MAX_UPLOAD_MB", "").strip()
    if _max_upload_mb:
        st.caption(f"This pilot version accepts packs up to {float(_max_upload_mb):.0f} MB. Please compress "
                  "the PDF or send a shorter pack.")
    code_status: pilot.CodeStatus | None = None
    if PILOT:
        store = pilot.PilotStore()
        locked = pilot.lockout_remaining(st.session_state)
        if locked:
            st.error(f"Too many wrong codes. Try again in {locked:.0f}s.", icon=None)
        else:
            raw_code = st.text_input("Pilot access code", key="pilot_code", placeholder="TL-XXXX-XXXX-XXXX-XXXX")
            if raw_code:
                code_status = store.validate(raw_code)
                pilot.record_attempt(st.session_state, code_status.ok)
                (st.success if code_status.ok else st.error)(code_status.message(), icon=None)
        with st.expander("Pilot terms"):
            st.caption(pilot.pilot_terms())
    rfqtender_ups = st.file_uploader("RFQ/Tender documents (PDF)", type="pdf", key="rfqtender",
                                     accept_multiple_files=True, help="Insert the original RFQ/Tender document(s).")
    if len(rfqtender_ups) > 10:
        st.error(f"{len(rfqtender_ups)} files selected; at most 10 RFQ/Tender documents are read.", icon=None)
    st.markdown("<div class='tl-step'>2 &middot; The bidder's response pack</div>", unsafe_allow_html=True)
    pack_ups = st.file_uploader("Response pack (PDF, scans are fine)", type="pdf", key="pack",
                                accept_multiple_files=True, help="Multiple files are read in the order "
                                "you add them, as one combined pack.")
    if len(pack_ups) > 10:
        st.error(f"{len(pack_ups)} files selected; at most 10 response pack files are read.", icon=None)
    if (llm.configured and not is_local(llm.base_url)) or (vision.configured and not is_local(vision.base_url)):
        _label = PROVIDER_LABEL.get(llm.provider, llm.provider)
        st.caption(f"A connected model reads your files off-device, at {_label} (USA). Upload only what "
                  "you're allowed to share.")

    st.markdown("<div class='tl-step'>3 &middot; Pack stage</div>", unsafe_allow_html=True)
    stage_col1, stage_col2 = st.columns(2)
    with stage_col1:
        if st.button("Draft pack", width="stretch",
                    type="primary" if st.session_state.get("pack_stage") == "draft" else "secondary",
                    help="Initials are skipped at this stage. A page with no initials in the bottom-right "
                         "corner is not flagged as an issue."):
            st.session_state["pack_stage"] = "draft"
    with stage_col2:
        if st.button("Final pack", width="stretch",
                    type="primary" if st.session_state.get("pack_stage") == "final" else "secondary",
                    help="Initials are checked at this stage. A page with no initials in the bottom-right "
                         "corner is flagged as an issue."):
            st.session_state["pack_stage"] = "final"
    pack_stage = st.session_state.get("pack_stage")
    if pack_stage is None:
        st.caption("Choose Draft pack or Final pack before auditing.")

    with st.expander("Settings"):
        bidder = st.text_input("Bidder name (for the report)")
        lc = "auto"  # always read from the buyer's own documents and the pack itself - never hand-set
        redact = st.toggle("Hide IDs, tax numbers and signatures in page images", value=False)
        strict = False
        check_choice = st.segmented_control("Check mode", ["Fast check", "Complete check"], default="Complete check",
                                            help="Fast: text and rules only, no vision calls. Complete: text and "
                                                 "vision together - handwriting, ticks, stamps and names are read "
                                                 "too, both always on.")
        fast_mode = check_choice == "Fast check"
        if not fast_mode and not vision.configured:
            st.warning("No vision model is connected, so Complete check can't read handwriting, ticks or stamps. "
                      "Switch to Fast check, or connect a vision model.", icon=None)
        inspect = not fast_mode
        second = not fast_mode

    if pack_ups:
        try:
            n_pages = sum(_cached_page_count(f.getvalue()) for f in pack_ups[:10])
            check_mode_now = "fast" if fast_mode else "complete"
            st.caption(page_count_estimate(n_pages, check_mode_now))
        except Exception:
            pass  # purely informational; a page-count problem surfaces properly when the real run reads the file

    st.markdown("<div class='tl-step'>4 &middot; Privacy</div>", unsafe_allow_html=True)
    st.caption(privacy.notice_short(llm.provider))
    consent = st.checkbox(privacy.CONSENT_LABEL, key="privacy_consent")

    ready = bool(rfqtender_ups and pack_ups and len(rfqtender_ups) <= 10 and len(pack_ups) <= 10
                and pack_stage is not None)
    if PILOT:
        ready = ready and bool(code_status and code_status.ok)
    ready = privacy.consent_required(ready, consent)
    run = st.button("Audit the pack", type="primary", width="stretch", disabled=not ready)

    st.divider()
    # Which provider actually answered this run's calls, never the key - see llm.py's MODEL_PROVIDER switch.
    provider_label = PROVIDER_LABEL.get(llm.provider, llm.provider)
    same_model = llm.configured and vision.configured and llm.model == vision.model and llm.base_url == vision.base_url
    if same_model:
        st.success(f"{provider_label}: {llm.model} (connected for both text and vision).", icon=None)
    else:
        if llm.configured:
            st.success(f"{provider_label} (text): {llm.model}", icon=None)
        else:
            st.info(f"No text model endpoint set (MODEL_PROVIDER={llm.provider}). Running on rules and OCR "
                    "only. See .env.example.", icon=None)
        if vision.configured:
            st.success(f"{provider_label} (vision): {vision.model}", icon=None)
        else:
            st.info("No vision model set, so handwriting, ticks and stamps are not read. See .env.example / "
                    "OLLAMA.md.", icon=None)

    st.markdown("<div class='tl-step'>System</div>", unsafe_allow_html=True)
    rows = _cached_system_check()
    bad = [item for item, ok, _ in rows if not ok]
    if not bad:
        st.success("Tesseract, Poppler and both Llama models are ready.", icon=None)
    else:
        st.warning("Not ready: " + ", ".join(bad), icon=None)
    with st.expander("Details", expanded=bool(bad)):
        for item, ok, detail in rows:
            st.write(("Ready: " if ok else "Missing: ") + item)
            if not ok and detail:
                st.caption(BUSY_MESSAGE if "429" in detail else detail)
    note = remote_warning(vision)
    if note:
        st.warning(note, icon=None)
    elif vision.configured:
        st.caption("Page images stay on this machine (local vision endpoint).")

# ----------------------------------------------------------------------------------- run

if run:
    t0 = time.time()
    tmp = WORK / "uploads"
    # Uploaded files and page images older than this are deleted on every run, not just under pilot - on a
    # host with limited/ephemeral disk (Cloud Run) these count toward memory, so stale ones should not pile
    # up. Default (24h) matches the previous pilot-only behaviour; set lower (e.g. 1) on a tighter host.
    # Today's own files are never touched here regardless of this setting - only ageing ones from earlier
    # runs/sessions - since "Run again" on an incomplete check needs this run's own page images to still
    # be there.
    purge_hours = float(os.environ.get("TENDERLENS_PURGE_HOURS", "24"))
    pilot.purge_old_files([WORK], purge_hours)
    claim_store = claim_hash = claim_file_sha = claim_kind = None
    facts, run_vision = None, vision  # safe defaults so the log below works even if we fail before audit_pack runs

    def log_check(complete: bool, credit_used: bool):
        try:
            check_log.record(
                check_mode="fast" if fast_mode else "complete", complete=complete,
                pages_failed=sorted(facts.vision_log.get("failed_pages") or []) if facts else [],
                vision_calls=run_vision.calls, vision_retries=run_vision.retries, text_retries=llm.retries,
                credit_used=credit_used)
        except Exception:
            pass  # the log must never be why a run fails

    with st.status("Auditing", expanded=True) as status:
        try:
            rfq = [save_upload(f, f"rfq-{i}-{f.name}", tmp) for i, f in enumerate(rfqtender_ups)]
            spec = None
            pack_sha = hashlib.sha1(b"".join(f.getvalue() for f in pack_ups)).hexdigest()[:8]
            pack = merge_uploads(pack_ups, f"pack-{pack_sha}.pdf", tmp)
            transcript = None
            if PILOT:
                claim_store, claim_hash = pilot.PilotStore(), code_status.code_hash
                claim_file_sha = pilot.file_sha256(pack)
                claim_kind = claim_store.claim_pack(claim_hash, claim_file_sha)  # raises NoPacksLeft on a race
            check_mode = "fast" if fast_mode else "complete"
            run_vision = LlamaClient(base_url="", model="") if fast_mode else vision
            st.write(f"Reading the buyer's documents and the response pack, then running the checks ({check_mode} check).")
            progress_line = st.empty()
            stage_label = {"ocr": "OCR", "vision_first": "vision first readings", "vision_checks": "vision checks"}
            main_ctx = get_script_run_ctx()

            def app_progress(stage, done, total):
                # called from OCR/vision worker threads, which have no Streamlit context of their own -
                # without this, any st.* call here raises NoSessionContext instead of updating the page.
                add_script_run_ctx(threading.current_thread(), main_ctx)
                progress_line.caption(f"{stage_label.get(stage, stage)}: {done} of {total}")

            req, res, facts, pages = audit_pack(rfq, spec, pack, BLANK, transcript, WORK, llm, lc, vision=run_vision,
                                                inspect="forms" if inspect else "off", passes=2 if second else 1,
                                                progress=app_progress, pack_stage=pack_stage)
            progress_line.empty()
            st.write("Building the report.")
            t_report = time.time()
            R = Renderer(pages, redact, strict)
            html = render_html(req, res, facts, pages, bidder or "Bidder", req.reference,
                               "Llama + rules" if llm.configured else "rules only", redact=redact, strict=strict,
                               check_mode=check_mode)
            facts.stage_times["report"] = time.time() - t_report
            check_complete = check_mode == "fast" or facts.vision_complete
            credit_used = False
            if claim_store is not None:
                n_issue = sum(1 for f in res.findings if f.tier == "issue")
                credit_used = claim_store.settle_pack(claim_hash, claim_file_sha, claim_kind, check_complete,
                                                      req.reference, n_issue)
            log_check(check_complete, credit_used)
            st.session_state["out"] = dict(req=req, res=res, facts=facts, pages=pages, R=R, html=html,
                                           json=to_json(req, res), secs=time.time() - t0, pack=pack.name,
                                           bidder=bidder or "Bidder", redact=redact, check_mode=check_mode,
                                           inspect=inspect, second=second, pack_stage=pack_stage,
                                           claim_hash=claim_hash, claim_file_sha=claim_file_sha,
                                           usage=dict(
                                               text_model=llm.model, text_calls=llm.calls,
                                               text_prompt_tokens=llm.prompt_tokens,
                                               text_completion_tokens=llm.completion_tokens,
                                               text_cost=llm.estimated_cost,
                                               vision_model=vision.model, vision_calls=vision.calls,
                                               vision_prompt_tokens=vision.prompt_tokens,
                                               vision_completion_tokens=vision.completion_tokens,
                                               vision_cost=vision.estimated_cost))
            status.update(label=f"Done in {time.time() - t0:.0f} s", state="complete", expanded=False)
        except pilot.NoPacksLeft:
            status.update(label="No packs left", state="error")
            st.error("That code's last pack was just used (by you or someone else at the same moment). "
                     "Ask for more packs if you need them.")
            st.stop()
        except FileNotFoundError as e:
            if claim_kind == "new":
                claim_store.refund_pack(claim_hash, claim_file_sha)
            log_check(False, False)
            status.update(label="A required program is missing", state="error")
            st.error(f"{e}. Install tesseract and poppler (see the System panel in the sidebar), then try again.")
            st.stop()
        except Exception as e:  # keep the demo alive and say what went wrong
            if claim_kind == "new":
                claim_store.refund_pack(claim_hash, claim_file_sha)
            log_check(False, False)
            busy = is_rate_limited(e)
            status.update(label="The AI service is busy" if busy else "Something went wrong", state="error")
            st.error(BUSY_MESSAGE if busy else f"{type(e).__name__}: {e}")
            st.stop()

out = st.session_state.get("out")

# ----------------------------------------------------------------------------------- landing

if not out:
    st.markdown("<p class='tl-brand'>Catch what the buyer would catch, before they do.</p>"
                "<p class='tl-sub'>TenderLens reads the RFQ/Tender, then checks your response pack against it: missing "
                "documents and pages, unsigned forms, quote arithmetic, spec mismatches, numbers that disagree "
                "between documents, and stray marks.</p>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**1. Understand the RFQ/Tender**")
        st.caption("Meta Llama reads the buyer's RFQ/Tender documents and specification into a checklist. Every item "
                   "must quote its source, or it is dropped.")
    with c2:
        st.markdown("**2. Read the response pack**")
        st.caption("Printed text is read by OCR. Handwriting and ticks are read by a vision model.")
    with c3:
        st.markdown("**3. Report, with evidence**")
        st.caption("Each issue shows the page, the rule it relates to, and what a person should check. "
                   "It never says disqualified; the buyer decides that.")
    st.stop()

# ----------------------------------------------------------------------------------- results

req, res, facts, pages, R = out["req"], out["res"], out["facts"], out["pages"], out["R"]
issues = [f for f in res.findings if f.tier == "issue"]
obs = [f for f in res.findings if f.tier != "issue"]
n_high = sum(1 for f in issues if f.severity == "high")

head, dl1, dl2 = st.columns([5, 1.2, 1.2])
with head:
    st.markdown(f"<p class='tl-brand'>{out['bidder']}</p><p class='tl-sub'>RFQ {req.reference} &middot; "
                f"{out['pack']} &middot; {len(pages)} pages &middot; {out['secs']:.0f} s</p>", unsafe_allow_html=True)
with dl1:
    st.download_button("Report (HTML)", out["html"], file_name="tenderlens_report.html", mime="text/html",
                       width="stretch")
with dl2:
    st.download_button("Data (JSON)", out["json"], file_name="tenderlens_report.json", mime="application/json",
                       width="stretch")

st.caption(DISCLAIMER_SHORT)
if out.get("check_mode") == "fast":
    st.info("**Mode: Fast check** (handwriting, ticks, stamps and names were not read). A clean Fast check "
            "says nothing about handwriting, ticks, stamps or names.", icon=None)
else:
    st.caption("Mode: Complete check")
    if not facts.vision_complete:
        failed_pages = sorted(facts.vision_log.get("failed_pages") or [])
        pages_txt = ", ".join(str(n) for n in failed_pages) if failed_pages else "not recorded"
        st.error("**Handwriting, ticks and stamps were NOT fully read in this run. This did not use one of "
                 f"your checks. Please run it again.**  \nPages that failed: {pages_txt}.", icon=None)
        rerun_claim_hash, rerun_file_sha = out.get("claim_hash"), out.get("claim_file_sha")
        rerun_store = pilot.PilotStore() if (PILOT and rerun_claim_hash and rerun_file_sha) else None
        if rerun_store and not rerun_store.rerun_allowed(rerun_claim_hash, rerun_file_sha):
            st.warning(f"Free re-runs for this pack are used up ({pilot.PilotStore.FREE_RERUN_LIMIT} so far). "
                      "Press \"Audit the pack\" again for another check (uses a pack as normal).", icon=None)
        elif st.button("Run again (free)", type="primary"):
            with st.status("Re-reading handwriting, ticks and stamps", expanded=True) as rstatus:
                rprogress = st.empty()
                r_stage_label = {"vision_first": "vision first readings", "vision_checks": "vision checks"}
                r_ctx = get_script_run_ctx()

                def rerun_progress(stage, done, total):
                    add_script_run_ctx(threading.current_thread(), r_ctx)
                    rprogress.caption(f"{r_stage_label.get(stage, stage)}: {done} of {total}")

                res2, facts2 = rerun_vision(out["req"], out["pages"], BLANK, vision,
                                            inspect="forms" if out["inspect"] else "off",
                                            passes=2 if out["second"] else 1, pack_stage=out["pack_stage"],
                                            progress=rerun_progress)
                rprogress.empty()
                if rerun_store:
                    rerun_store.record_rerun(rerun_claim_hash, rerun_file_sha)
                try:
                    check_log.record(check_mode="complete", complete=facts2.vision_complete,
                                     pages_failed=sorted(facts2.vision_log.get("failed_pages") or []),
                                     vision_calls=vision.calls, vision_retries=vision.retries,
                                     text_retries=llm.retries, credit_used=False, free_rerun=True)
                except Exception:
                    pass
                R2 = Renderer(out["pages"], out["redact"], False)
                html2 = render_html(out["req"], res2, facts2, out["pages"], out["bidder"], out["req"].reference,
                                    "Llama + rules" if llm.configured else "rules only", redact=out["redact"],
                                    check_mode="complete")
                st.session_state["out"] = {**out, "res": res2, "facts": facts2, "R": R2, "html": html2,
                                           "json": to_json(out["req"], res2)}
                rstatus.update(label="Done" if facts2.vision_complete else "Still incomplete",
                              state="complete" if facts2.vision_complete else "error", expanded=False)
            st.rerun()
if issues:
    st.markdown(f"<div class='tl-banner tl-bad'><b style='color:#b3261e'>{len(issues)} issue"
                f"{'s' if len(issues) != 1 else ''} to review</b><br><span>{n_high} likely to matter &middot; "
                f"{len(issues) - n_high} worth checking &middot; {len(obs)} observations &middot; "
                f"{len(res.passed)} checks passed</span></div>", unsafe_allow_html=True)
else:
    st.markdown(f"<div class='tl-banner tl-ok'><b style='color:#2e7d32'>No issues found</b><br>"
                f"<span>{len(res.passed)} checks passed &middot; {len(obs)} observation"
                f"{'s' if len(obs) != 1 else ''} for information</span><br>"
                f"<span>{NO_ISSUES_NOTE}</span></div>", unsafe_allow_html=True)

m = st.columns(4)
m[0].metric("Issues", len(issues))
m[1].metric("Observations", len(obs))
m[2].metric("Checks passed", len(res.passed))
m[3].metric("Pages scanned", len(pages))

cited = sorted({ev.page for f in res.findings for ev in f.evidence})
tab_issues, tab_req, tab_pass, tab_pages, tab_how = st.tabs(
    [f"Issues ({len(issues)})", "What the buyer asked for", f"Checks passed ({len(res.passed)})", "Pages",
     "How this was read"])


def finding_card(f):
    color = SEV[f.severity][1] if f.tier == "issue" else "#4a6572"
    label = SEV[f.severity][0] if f.tier == "issue" else "Observation"
    with st.container(border=True):
        st.markdown(f"{chip(label, color)}<span class='tl-title'>{f.title}</span>", unsafe_allow_html=True)
        st.caption(f"{f.id} · {f.category.replace('_', ' ')}")
        st.write(f.detail)
        st.markdown("<div class='tl-k'>What needs review</div>", unsafe_allow_html=True)
        st.write(f.action)
        if f.rule:
            st.markdown("<div class='tl-k'>Source / rule</div>", unsafe_allow_html=True)
            st.caption(f.rule)
        if f.evidence:
            st.markdown("<div class='tl-k'>Evidence</div>", unsafe_allow_html=True)
            evs = f.evidence[:3]
            cols = st.columns(len(evs))
            for col, ev in zip(cols, evs):
                dt = DOC_LABEL.get(res.page_types.get(ev.page, ""), "")
                cap = f"Page {ev.page}" + (f" · {dt}" if dt else "") + (f" · {ev.note}" if ev.note else "") \
                    + f"  [{SRC_LABEL.get(ev.source, ev.source)}]"
                with col:
                    if ev.box is not None:
                        st.image(b64_to_bytes(R.crop(ev)), caption=cap, width="stretch")
                    else:
                        st.image(R.page_img(ev.page), caption=cap + " (whole page)", width="stretch")


with tab_issues:
    if issues:
        for f in issues:
            finding_card(f)
    else:
        st.success("Every requirement the tool can check was met, and no document disagrees with another.")
    if obs:
        with st.expander(f"Observations ({len(obs)}): worth knowing, not failures"):
            for f in obs:
                finding_card(f)

with tab_req:
    c1, c2 = st.columns(2)
    c1.metric("Closing", f"{req.closing_date}", f"at {req.closing_time}", delta_color="off")
    c2.metric("Minimum quote validity", f"{req.min_quote_validity_days} days")
    st.markdown(f"**Submit to:** {req.submit_to_email}")
    if req.spec:
        st.markdown(f"**Specification:** {req.spec.get('quantity')} × {req.spec.get('description')}, "
                    f"deliver to {req.spec.get('delivery_address')}")
    st.markdown(f"**Local content required:** {'yes' if req.local_content_required else 'no'}")
    st.caption(req.local_content_basis)
    st.markdown("#### Required documents")
    for d in req.documents:
        st.markdown(f"- **{d.label}**" + (f" _({d.conditional})_" if d.conditional else ""))
        st.caption(f"“{d.source_quote[:220]}”")
    if req.conditions:
        st.markdown("#### Conditions")
        for c in req.conditions:
            st.markdown(f"- **{c.label}**")
            st.caption(f"“{c.source_quote[:220]}”")
    if req.notes:
        st.caption("Method notes: " + " ".join(req.notes))

with tab_pass:
    q = st.text_input("Filter", placeholder="signature, quote, SBD 4 ...", label_visibility="collapsed")
    shown = [p for p in res.passed if q.lower() in (p.title + " " + p.note).lower()]
    st.caption(f"{len(shown)} of {len(res.passed)}")
    for p in shown:
        pg = f"  ·  p. {', '.join(map(str, p.pages))}" if p.pages else ""
        st.markdown(f"✓ {p.title}{pg}")
        if p.note:
            st.caption(p.note)
    if res.ocr_variants:
        with st.expander("Possible misreads that were set aside"):
            for v in res.ocr_variants:
                st.write("• " + v)

with tab_pages:
    flagged = {}
    for f in res.findings:
        for ev in f.evidence:
            flagged.setdefault(ev.page, []).append(f.id)
    st.caption("Pages with a finding are marked. Pick a page to see it larger"
               + (" (IDs and signatures hidden)." if out["redact"] else ". Images are NOT redacted."))
    pick = st.selectbox("Page", [None] + [p.number for p in pages],
                        format_func=lambda n: "Choose a page" if n is None else
                        f"Page {n} · {DOC_LABEL.get(res.page_types.get(n, ''), res.page_types.get(n, ''))}"
                        + (f" · {', '.join(flagged[n])}" if n in flagged else ""),
                        label_visibility="collapsed")
    if pick:
        st.image(R.page_img(pick), width="stretch")
    cols = st.columns(6)
    for i, p in enumerate(pages):
        with cols[i % 6]:
            st.image(R.page_img(p.number), width="stretch")
            mark = " ●" if p.number in flagged else ""
            st.caption(f"{p.number} · {DOC_LABEL.get(p.doc_type, p.doc_type)}{mark}")

with tab_how:
    st.info(DISCLAIMER_LONG, icon=None)
    src = Counter(f.source for f in facts.items if f.field != "page.doc_type")
    st.markdown("**Facts by source:** " + ", ".join(f"{k} {v}" for k, v in sorted(src.items())))
    st.markdown("**Requirements read by:** " + (f"Llama ({llm.model}) cross-checked with rules" if llm.configured
                                               else "rules only (no Llama endpoint configured)"))
    vl = facts.vision_log
    if vl.get("stopped_early"):
        st.warning(f"A vision model is connected ({vl.get('model', vision.model)}) but its calls failed, so the "
                   f"handwriting steps were stopped early rather than repeating the same failure: "
                   f"{vl['stopped_early']}", icon=None)
    elif vl.get("calls") and not vl.get("pages_read") and vl.get("failed") == vl.get("calls"):
        st.warning(f"A vision model is connected ({vl.get('model', vision.model)}) but every one of its "
                   f"{vl['calls']} call(s) failed. Reason(s): " + "; ".join(vl.get("errors") or ["not recorded"]),
                   icon=None)
    elif vl.get("calls"):
        st.markdown(f"**Handwriting read by:** {vl.get('model', vision.model)} ({vl['pages_read']} pages read, "
                    f"{vl['pages_inspected']} inspected, {vl['calls']} calls, {vl['failed']} failed)")
        if vl.get("failed"):
            st.caption("Some calls failed: " + "; ".join(vl.get("errors") or []))

    stage_names = [("render", "Page rendering"), ("ocr", "OCR"), ("text_model", "Text model (requirements)"),
                  ("vision_first", "Vision first readings"), ("vision_second", "Vision second looks and checks"),
                  ("report", "Report")]
    times = getattr(facts, "stage_times", None) or {}
    if any(k in times for k, _ in stage_names):
        st.markdown("#### Timing")
        st.caption("Page rendering and the text model ran concurrently, so they overlap in wall-clock time "
                  "rather than add up.")
        for key, label in stage_names:
            if key in times:
                st.write(f"• {label}: {times[key]:.1f}s")

    u = out.get("usage") or {}
    if u.get("text_calls") or u.get("vision_calls"):
        st.markdown("#### API usage")
        rows = []
        for label, prefix in (("Text", "text"), ("Vision", "vision")):
            calls = u.get(f"{prefix}_calls", 0)
            if not calls:
                continue
            pin, pout = u.get(f"{prefix}_prompt_tokens", 0), u.get(f"{prefix}_completion_tokens", 0)
            cost = u.get(f"{prefix}_cost")
            rows.append(f"{label} ({u.get(f'{prefix}_model')}): {calls} call(s), {pin:,} prompt tokens, "
                       f"{pout:,} completion tokens" + (f", est. ${cost:.4f}" if cost is not None else ""))
        for r in rows:
            st.write("• " + r)
        if u.get("text_cost") is None and u.get("vision_cost") is None:
            st.caption("No price per token is set (TENDERLENS_LLM_PRICE_IN/_OUT, TENDERLENS_VISION_PRICE_IN/_OUT), "
                      "so only token counts are shown, not an estimated cost.")

    st.write("Printed text is read by OCR. Handwriting and ticks are read by a multimodal Llama model or a human "
             "transcription; anything marked manual should be spot-checked. Signature, stray-mark and blank-page "
             "findings come from image analysis, which is probabilistic. All comparisons, sums and date checks are "
             "ordinary code, so they are repeatable.")
    if res.human_only:
        st.markdown("#### Only a person can confirm")
        for h in res.human_only:
            st.write("• " + h)
