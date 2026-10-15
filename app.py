"""TenderLens - Streamlit interface.   Run:  streamlit run app.py

Upload the buyer's RFQ documents (and specification, if there is one), then the bidder's response pack, and press Audit.
Everything on screen comes from tenderlens.cli.audit_pack, the same pipeline as the command line.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from collections import Counter
from pathlib import Path

import streamlit as st

from tenderlens import pilot
from tenderlens.audit import DOC_LABEL
from tenderlens.bootstrap import ensure_local_models, remote_warning, system_check
from tenderlens.cli import audit_pack
from tenderlens.llm import LlamaClient, load_env
from tenderlens.report import SEV, SRC_LABEL, Renderer, render_html, to_json

load_env()
try:  # hosted (e.g. Streamlit Community Cloud): its Secrets become env vars our config already reads.
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass  # no secrets.toml locally - fine, that's what .env / the environment is for
PILOT = pilot.pilot_enabled()  # TENDERLENS_PILOT=1. Off: the app behaves exactly as it always has.

ROOT = Path(__file__).resolve().parent
WORK = ROOT / ".cache"
BLANK = ROOT / "samples" / "blank"
SAMPLE_RFQ = ROOT / "samples" / "rfq" / "rfq_email.pdf"
SAMPLE_SPEC = ROOT / "samples" / "rfq" / "spec_0310.pdf"
SAMPLE_PACK = ROOT / "samples" / "response" / "pack.pdf"
SAMPLE_TRANSCRIPT = ROOT / "samples" / "response" / "pack.transcript.json"
TAMPER_DIR = ROOT / "out" / "tamper"

st.set_page_config(page_title="TenderLens", page_icon=None, layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
#MainMenu, footer {visibility:hidden}
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


def b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)


def chip(label: str, color: str) -> str:
    return f"<span class='tl-chip' style='background:{color}'>{label}</span>"


def pretty(folder: str) -> str:
    """'01_remove_sbd8_signing_page' -> 'Remove SBD 8 signing page'"""
    t = folder[3:].replace("sbd6_1", "SBD 6.1").replace("sbd6_2", "SBD 6.2")
    t = re.sub(r"sbd(\d)", r"SBD \1", t).replace("_", " ")
    for a, b in (("bbbee", "B-BBEE"), ("csd", "CSD"), ("cipc", "CIPC"), ("sa id", "SA ID"), ("id number", "ID number")):
        t = re.sub(rf"\b{a}\b", b, t)
    return t[:1].upper() + t[1:]


def tamper_variants() -> list[Path]:
    return sorted(TAMPER_DIR.glob("*/pack.pdf")) if TAMPER_DIR.exists() else []


# ----------------------------------------------------------------------------------- sidebar

llm = LlamaClient()
vision = LlamaClient.for_vision(llm)
with st.sidebar:
    st.markdown("<p class='tl-brand'>TenderLens</p><p class='tl-sub'>Audit an RFQ response before you submit it</p>",
                unsafe_allow_html=True)

    st.markdown("<div class='tl-step'>1 &middot; What the buyer sent</div>", unsafe_allow_html=True)
    have_sample = SAMPLE_RFQ.exists() and SAMPLE_SPEC.exists() and SAMPLE_PACK.exists()
    options = ["Upload my own files"] + (["Use the sample RFQ (0310)"] if have_sample else [])
    mode = st.radio("Source", options, label_visibility="collapsed")

    rfq_ups: list = []
    spec_up = pack_up = transcript_up = None
    pack_choice: Path | None = None
    code_status: pilot.CodeStatus | None = None
    if mode == "Upload my own files":
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
                st.caption(pilot.PILOT_TERMS)
        rfq_ups = st.file_uploader("RFQ documents (PDF)", type="pdf", key="rfq", accept_multiple_files=True)
        st.caption("The buyer's cover email or letter, plus anything it came with as a separate file - annexures, "
                  "a BOQ, blank SBD templates if sent that way. Up to 10 files; order matters only in that the "
                  "closing date and other requirements are found wherever they are, in whichever file they're in. "
                  "Not the specification (below) and not the bidder's own response pack.")
        if len(rfq_ups) > 10:
            st.error(f"{len(rfq_ups)} files selected; at most 10 RFQ documents are read.", icon=None)
        spec_up = st.file_uploader("Specification (PDF, optional)", type="pdf", key="spec",
                                   help="Only if the buyer issued one separately from the RFQ. Some RFQs state the "
                                        "specification inline, or need none.")
        st.markdown("<div class='tl-step'>2 &middot; The bidder's response pack</div>", unsafe_allow_html=True)
        pack_up = st.file_uploader("Response pack (PDF, scans are fine)", type="pdf", key="pack")
        if not PILOT:
            transcript_up = st.file_uploader("Handwriting transcript (optional, JSON)", type="json", key="tr",
                                             help="Only needed while no vision model is attached.")
    else:
        st.markdown("<div class='tl-step'>2 &middot; The bidder's response pack</div>", unsafe_allow_html=True)
        variants = {"The pack that won (untouched)": SAMPLE_PACK}
        variants.update({pretty(p.parent.name): p for p in tamper_variants() if not p.parent.name.startswith("00")})
        label = st.selectbox("Pack", list(variants), help="The other entries are the winning pack with one thing "
                             "changed, to show what TenderLens catches.")
        pack_choice = variants[label]

    with st.expander("Settings"):
        bidder = st.text_input("Bidder name (for the report)", value="Excel Supply Holdings" if mode != "Upload my own files" else "")
        lc = st.selectbox("Local content applies?", ["auto", "yes", "no"],
                          help="auto reads the buyer's documents. SBD 6.2 and Annexure C are only required when the "
                               "buyer says local content applies.")
        redact = st.toggle("Hide IDs, tax numbers and signatures in page images", value=True)
        strict = st.toggle("Also hide registration and CSD numbers", value=False, disabled=not redact)
        inspect = st.toggle("Vision: look for crossed-out text, notes, rotated pages", value=True, disabled=not vision.configured,
                            help="Needs a vision model. Adds one question per handwritten page (slow on a laptop CPU).")
        second = st.toggle("Vision: second look at every suspicious reading", value=True, disabled=not vision.configured,
                           help="A 'Yes', a blank or a double tick is asked again, worded differently, before it counts as an issue.")

    ready = (mode != "Upload my own files") or bool(rfq_ups and pack_up and len(rfq_ups) <= 10)
    if mode == "Upload my own files" and PILOT:
        ready = ready and bool(code_status and code_status.ok)
    run = st.button("Audit the pack", type="primary", width="stretch", disabled=not ready)
    if mode == "Upload my own files" and not ready:
        st.caption("Add at least one RFQ document and the response pack to begin (the specification is optional)."
                  + (" A valid pilot code is also needed." if PILOT else ""))

    st.divider()
    if llm.configured:
        st.success(f"Llama (text): {llm.model}", icon=None)
    else:
        st.info("No Llama text endpoint set. Running on rules and OCR only. Set TENDERLENS_LLM_BASE_URL and "
                "TENDERLENS_LLM_MODEL to use Meta Llama.", icon=None)
    if vision.configured:
        st.success(f"Llama (vision): {vision.model}", icon=None)
    else:
        st.info("No vision model set, so handwriting, ticks and stamps are not read (a transcript can stand in). "
                "See OLLAMA.md.", icon=None)

    st.markdown("<div class='tl-step'>System</div>", unsafe_allow_html=True)
    rows = system_check()
    bad = [item for item, ok, _ in rows if not ok]
    if not bad:
        st.success("Tesseract, Poppler and both Llama models are ready.", icon=None)
    else:
        st.warning("Not ready: " + ", ".join(bad), icon=None)
    with st.expander("Details", expanded=bool(bad)):
        for item, ok, detail in rows:
            st.write(("Ready: " if ok else "Missing: ") + item)
            if not ok and detail:
                st.caption(detail)
        if st.button("Start Ollama and fetch the model", width="stretch"):
            log = st.empty()
            lines = []

            def add_line(msg):
                lines.append(msg)
                log.write("\n".join(lines))

            good = ensure_local_models(log=add_line)
            (st.success if good else st.error)("Ready." if good else "Still not ready; see the message above.")
            if good:
                st.rerun()
    note = remote_warning(vision)
    if note:
        st.warning(note, icon=None)
    elif vision.configured:
        st.caption("Page images stay on this machine (local vision endpoint).")

# ----------------------------------------------------------------------------------- run

if run:
    t0 = time.time()
    tmp = WORK / "uploads"
    if PILOT:
        pilot.purge_old_files([WORK], 24)  # uploaded files and page images, 24h after they were made
    claim_store = claim_hash = claim_file_sha = claim_kind = None
    with st.status("Auditing", expanded=True) as status:
        try:
            if mode == "Upload my own files":
                rfq = [save_upload(f, f"rfq-{i}-{f.name}", tmp) for i, f in enumerate(rfq_ups)]
                spec = save_upload(spec_up, "spec.pdf", tmp) if spec_up else None
                pack_bytes = pack_up.getvalue()
                pack = save_upload(pack_up, f"pack-{hashlib.sha1(pack_bytes).hexdigest()[:8]}.pdf", tmp)
                transcript = save_upload(transcript_up, "transcript.json", tmp) if transcript_up else None
                if PILOT:
                    claim_store, claim_hash = pilot.PilotStore(), code_status.code_hash
                    claim_file_sha = pilot.file_sha256(pack)
                    claim_kind = claim_store.claim_pack(claim_hash, claim_file_sha)  # raises NoPacksLeft on a race
            else:
                rfq, spec, pack = SAMPLE_RFQ, SAMPLE_SPEC, pack_choice
                own = pack.parent / "transcript.json"   # a tamper variant may carry its own handwriting reading
                transcript = own if own.exists() else (SAMPLE_TRANSCRIPT if SAMPLE_TRANSCRIPT.exists() else None)
            st.write("Reading the buyer's documents and the response pack, then running the checks.")
            req, res, facts, pages = audit_pack(rfq, spec, pack, BLANK, transcript, WORK, llm, lc, vision=vision,
                                                inspect="forms" if inspect else "off", passes=2 if second else 1)
            st.write("Building the report.")
            R = Renderer(pages, redact, strict)
            html = render_html(req, res, facts, pages, bidder or "Bidder", req.reference,
                               "Llama + rules" if llm.configured else "rules only", redact=redact, strict=strict)
            if claim_kind == "new":
                n_issue = sum(1 for f in res.findings if f.tier == "issue")
                claim_store.finalize_pack(claim_hash, claim_file_sha, req.reference, n_issue)
            st.session_state["out"] = dict(req=req, res=res, facts=facts, pages=pages, R=R, html=html,
                                           json=to_json(req, res), secs=time.time() - t0, pack=pack.name,
                                           bidder=bidder or "Bidder", redact=redact)
            status.update(label=f"Done in {time.time() - t0:.0f} s", state="complete", expanded=False)
        except pilot.NoPacksLeft:
            status.update(label="No packs left", state="error")
            st.error("That code's last pack was just used (by you or someone else at the same moment). "
                     "Ask for more packs if you need them.")
            st.stop()
        except FileNotFoundError as e:
            if claim_kind == "new":
                claim_store.refund_pack(claim_hash, claim_file_sha)
            status.update(label="A required program is missing", state="error")
            st.error(f"{e}. Install tesseract and poppler (see the System panel in the sidebar), then try again.")
            st.stop()
        except Exception as e:  # keep the demo alive and say what went wrong
            if claim_kind == "new":
                claim_store.refund_pack(claim_hash, claim_file_sha)
            status.update(label="Something went wrong", state="error")
            st.error(f"{type(e).__name__}: {e}")
            st.stop()

out = st.session_state.get("out")

# ----------------------------------------------------------------------------------- landing

if not out:
    st.markdown("<p class='tl-brand'>Catch what the buyer would catch, before they do.</p>"
                "<p class='tl-sub'>TenderLens reads the RFQ, then checks your response pack against it: missing "
                "documents and pages, unsigned forms, quote arithmetic, spec mismatches, numbers that disagree "
                "between documents, and stray marks.</p>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**1. Understand the RFQ**")
        st.caption("Meta Llama reads the buyer's RFQ documents and specification into a checklist. Every item must quote "
                   "its source, or it is dropped.")
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

if issues:
    st.markdown(f"<div class='tl-banner tl-bad'><b style='color:#b3261e'>{len(issues)} issue"
                f"{'s' if len(issues) != 1 else ''} to review</b><br><span>{n_high} likely to matter &middot; "
                f"{len(issues) - n_high} worth checking &middot; {len(obs)} observations &middot; "
                f"{len(res.passed)} checks passed</span></div>", unsafe_allow_html=True)
else:
    st.markdown(f"<div class='tl-banner tl-ok'><b style='color:#2e7d32'>No issues found</b><br>"
                f"<span>{len(res.passed)} checks passed &middot; {len(obs)} observation"
                f"{'s' if len(obs) != 1 else ''} for information</span></div>", unsafe_allow_html=True)

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
        with st.expander("Possible scanner misreads that were set aside"):
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
    src = Counter(f.source for f in facts.items if f.field != "page.doc_type")
    st.markdown("**Facts by source:** " + ", ".join(f"{k} {v}" for k, v in sorted(src.items())))
    st.markdown("**Requirements read by:** " + (f"Llama ({llm.model}) cross-checked with rules" if llm.configured
                                               else "rules only (no Llama endpoint configured)"))
    vl = facts.vision_log
    if vl.get("calls"):
        st.markdown(f"**Handwriting read by:** {vl.get('model', vision.model)} ({vl['pages_read']} pages read, "
                    f"{vl['pages_inspected']} inspected, {vl['calls']} calls, {vl['failed']} failed)")
    st.write("Printed text is read by OCR. Handwriting and ticks are read by a multimodal Llama model or a human "
             "transcription; anything marked manual should be spot-checked. Signature, stray-mark and blank-page "
             "findings come from image analysis, which is probabilistic. All comparisons, sums and date checks are "
             "ordinary code, so they are repeatable.")
    if res.human_only:
        st.markdown("#### Only a person can confirm")
        for h in res.human_only:
            st.write("• " + h)
