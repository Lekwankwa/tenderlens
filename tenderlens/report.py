"""Step 4 - the report: every issue cites its page, the rule it relates to, and how the fact was obtained."""
from __future__ import annotations

import base64
import html
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .audit import AuditResult, DOC_LABEL, Evidence, Finding
from .facts import Facts
from .ingest import Page
from .redact import redact_image
from .requirements import Requirements

SEV = {
    "high": ("Likely to matter", "#b3261e"),
    "medium": ("Worth checking", "#b26a00"),
    "low": ("Minor", "#4a6572"),
    "info": ("For information", "#5f6b73"),
}
SRC_LABEL = {"ocr": "read by OCR", "manual": "manual transcription", "vision": "read by Llama vision",
             "image": "image analysis"}

# One wording, used everywhere a disclaimer is shown (report banner, "How this was read", the demo, the README),
# so it never drifts between places. See Step 5 of FIX_ALL_PROMPT.md.
DISCLAIMER_SHORT = (
    "This report was produced with AI (Meta's Llama 4 Scout) and fixed rules. AI can make mistakes and miss things. "
    "TenderLens is a review aid, not a verdict: check anything that matters against the original documents.")
DISCLAIMER_LONG = (
    "Handwriting, ticks, stamps and signatures are read by an AI vision model and can be misread, including digits, "
    "yes/no answers and names. Anything based on handwriting is a lead to check against the page image, not a finding "
    "of fact. The AI may also misread the buyer's requirements, even though they are cross-checked against rules and "
    "a source quotation. A report with no issues does not mean the bid is compliant or will be accepted, and a report "
    "with issues may contain false alarms. TenderLens does not judge whether a signature, stamp or certificate is "
    "genuine, does not check live registers, and is not legal advice. The buyer decides what disqualifies a bid.")
NO_ISSUES_NOTE = "No issues found. That is not a guarantee that the bid complies."

# What Fast check (no vision calls) cannot do, shown in the report so a clean Fast result is never mistaken
# for a clean Complete one.
VISION_DEPENDENT_CHECKS = [
    "Handwritten entries on SBD 4, 6.1, 8, 9 and the local content declaration (ticks, dates, names, amounts, witness counts)",
    "Crossed-out or overwritten text, handwritten notes outside the form fields, rotated or cut-off pages",
    "Whether a signing line holds a real pen signature",
    "Whether two signatures may be in different hands",
    "The B-BBEE sworn affidavit's commissioner stamp and signature",
]


def _jpeg_b64(im: Image.Image, q=72) -> str:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return base64.b64encode(buf.getvalue()).decode()


class Renderer:
    def __init__(self, pages: list[Page], redact: bool, strict: bool):
        self.pages = {p.number: p for p in pages}
        self.redact, self.strict = redact, strict
        self._cache: dict[int, Image.Image] = {}

    def page_img(self, n: int) -> Image.Image:
        if n not in self._cache:
            p = self.pages[n]
            if self.redact:
                self._cache[n] = redact_image(p, self.strict)[0]
            else:
                self._cache[n] = Image.open(p.image_path).convert("RGB")
        return self._cache[n]

    def crop(self, ev: Evidence) -> str:
        im = self.page_img(ev.page).copy()
        x0, y0, x1, y1 = (int(v) for v in ev.box)
        d = ImageDraw.Draw(im)
        d.rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), outline=(200, 30, 30), width=5)
        l, t, r, b = ev.pad
        c = im.crop((max(0, x0 - l - 120), max(0, y0 - t - 90), min(im.width, x1 + r + 120), min(im.height, y1 + b + 90)))
        if c.width > 900:
            c = c.resize((900, int(c.height * 900 / c.width)))
        return _jpeg_b64(c)

    def thumb(self, n: int, width=640) -> str:
        im = self.page_img(n)
        return _jpeg_b64(im.resize((width, int(im.height * width / im.width))), 62)


CSS = """
:root{--bg:#fbfaf8;--fg:#1d2327;--mut:#5f6b73;--line:#e1ddd6;--card:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#171a1c;--fg:#e8e6e2;--mut:#9aa5ab;--line:#2e3438;--card:#1f2326}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.15rem;margin:2rem 0 .6rem;border-bottom:1px solid var(--line);padding-bottom:.3rem}
.mut{color:var(--mut)}.sub{color:var(--mut);font-size:.9rem}
.banner{background:var(--card);border:1px solid var(--line);border-left-width:6px;border-radius:10px;padding:12px 14px;margin:14px 0;font-size:1.05rem}
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.stat{flex:1 1 120px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat b{display:block;font-size:1.5rem}
.card{background:var(--card);border:1px solid var(--line);border-left-width:5px;border-radius:10px;padding:12px 14px;margin:12px 0}
.card h3{margin:0 0 4px;font-size:1.02rem}.chip{display:inline-block;font-size:.72rem;font-weight:600;color:#fff;border-radius:99px;padding:1px 9px;margin-right:6px;vertical-align:middle}
.k{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin-top:8px}
.ev{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}.ev figure{margin:0;flex:1 1 260px;max-width:100%}
.ev img{width:100%;border:1px solid var(--line);border-radius:6px}.ev figcaption{font-size:.8rem;color:var(--mut);margin-top:2px}
.tag{font-size:.7rem;border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--mut);margin-left:4px}
table{border-collapse:collapse;width:100%;font-size:.88rem}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
details{margin:8px 0}summary{cursor:pointer;font-weight:600}ul{padding-left:1.2rem}li{margin:3px 0}
.note{background:var(--card);border:1px dashed var(--line);border-radius:8px;padding:10px 12px;font-size:.9rem}
.pages{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}.pages img{width:100%;border:1px solid var(--line)}
"""


def render_html(req: Requirements, res: AuditResult, facts: Facts, pages: list[Page], bidder: str,
                rfq_ref: str, llm_mode: str, redact: bool = True, strict: bool = False,
                check_mode: str = "complete") -> str:
    R = Renderer(pages, redact, strict)
    e = html.escape
    src_count = Counter(f.source for f in facts.items if f.field != "page.doc_type")
    cited: set[int] = set()
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>TenderLens audit</title><style>{CSS}</style></head><body><main>"]
    out.append(f"<h1>TenderLens audit report</h1><div class=sub>{e(bidder)} &middot; RFQ {e(rfq_ref)} &middot; "
               f"generated {datetime.now():%Y-%m-%d %H:%M}</div>")
    if check_mode == "fast":
        out.append("<p class=note><b>Mode: Fast check</b> (handwriting, ticks, stamps and names were not read)</p>"
                   "<div class=note><b>Not checked in this mode:</b><ul>"
                   + "".join(f"<li>{e(c)}</li>" for c in VISION_DEPENDENT_CHECKS)
                   + "</ul><p>A clean Fast check says nothing about handwriting, ticks, stamps or names.</p></div>")
    else:
        out.append("<p class=note><b>Mode: Complete check</b></p>")
    out.append(f"<div class=banner>{e(DISCLAIMER_SHORT)}</div>")
    out.append("<p class=note>Each item says what was found, where, which rule it relates to and how the fact was "
               "read. The buyer decides what disqualifies a bid. Images below "
               f"{'have personal identifiers and signatures blacked out' if redact else 'are NOT redacted: do not share this file'}.</p>")
    issues = [f for f in res.findings if f.tier == "issue"]
    obs = [f for f in res.findings if f.tier != "issue"]
    n_high = sum(1 for f in issues if f.severity == "high")
    if issues:
        out.append(f"<div class=banner style='border-color:#b3261e'><b style='color:#b3261e'>{len(issues)} "
                   f"issue{'s' if len(issues) != 1 else ''} to review</b> &middot; {len(obs)} observation"
                   f"{'s' if len(obs) != 1 else ''} &middot; {len(res.passed)} checks passed</div>")
    else:
        out.append(f"<div class=banner style='border-color:#2e7d32'><b style='color:#2e7d32'>No issues found</b> "
                   f"&middot; {len(res.passed)} checks passed &middot; {len(obs)} observation"
                   f"{'s' if len(obs) != 1 else ''} for information<br><span class=sub>{e(NO_ISSUES_NOTE)}</span></div>")
    out.append("<div class=stats>"
               f"<div class=stat><b style='color:#b3261e'>{n_high}</b>Likely to matter</div>"
               f"<div class=stat><b style='color:#b26a00'>{len(issues) - n_high}</b>Worth checking</div>"
               f"<div class=stat><b style='color:#4a6572'>{len(obs)}</b>Observations</div>"
               f"<div class=stat><b style='color:#2e7d32'>{len(res.passed)}</b>Checks passed</div></div>")

    # requirements
    out.append("<h2>1. What the buyer asked for</h2><div class=sub>Established from the RFQ and specification. "
               "Each line quotes the source text.</div>")
    rows = [("Closing", f"{req.closing_date} at {req.closing_time}", "")]
    rows += [("Submit to", req.submit_to_email, ""), ("Minimum quote validity", f"{req.min_quote_validity_days} days", "")]
    if req.spec:
        rows.append(("Specification", f"{req.spec.get('quantity')} x {req.spec.get('description')}, deliver to "
                     f"{req.spec.get('delivery_address')}", req.spec.get("source_doc", "")))
    out.append("<table>" + "".join(f"<tr><th>{e(a)}</th><td>{e(str(b))}</td></tr>" for a, b, _ in rows) + "</table>")
    out.append("<details><summary>Required documents and conditions (with source quotes)</summary><table>")
    for d in req.documents:
        out.append(f"<tr><td>{e(d.label)}</td><td class=mut>&ldquo;{e(d.source_quote[:140])}&rdquo;"
                   f"{' <i>(' + e(d.conditional) + ')</i>' if d.conditional else ''}</td></tr>")
    for c in req.conditions:
        out.append(f"<tr><td>{e(c.label)}</td><td class=mut>&ldquo;{e(c.source_quote[:140])}&rdquo;</td></tr>")
    out.append("</table></details>")
    out.append(f"<div class=sub>Requirements method: {e(llm_mode)}. " + e(" ".join(req.notes)) + "</div>")

    # findings
    def card(fnd):
        label, color = SEV[fnd.severity] if fnd.tier == "issue" else ("Observation", "#4a6572")
        out.append(f"<div class=card style='border-left-color:{color}'><h3><span class=chip style='background:{color}'>"
                   f"{label}</span>{e(fnd.title)}</h3><div class=sub>{fnd.id} &middot; {e(fnd.category.replace('_', ' '))}</div>"
                   f"<p>{e(fnd.detail)}</p><div class=k>What needs review</div><p>{e(fnd.action)}</p>")
        if fnd.rule:
            out.append(f"<div class=k>Source / rule</div><p class=mut>{e(fnd.rule)}</p>")
        if fnd.evidence:
            out.append("<div class=k>Evidence</div><div class=ev>")
            for ev in fnd.evidence[:4]:
                cited.add(ev.page)
                dt = DOC_LABEL.get(res.page_types.get(ev.page, ""), "")
                cap = f"Pack page {ev.page}" + (f" &middot; {e(dt)}" if dt else "") + (f" &middot; {e(ev.note)}" if ev.note else "")
                cap += f"<span class=tag>{e(SRC_LABEL.get(ev.source, ev.source))}</span>"
                if ev.box is not None:
                    out.append(f"<figure><img alt='' src='data:image/jpeg;base64,{R.crop(ev)}'><figcaption>{cap}</figcaption></figure>")
                else:
                    out.append(f"<figure><figcaption>{cap} &mdash; see appendix</figcaption></figure>")
            out.append("</div>")
        out.append("</div>")

    out.append("<h2>2. Issues to review</h2>")
    if issues:
        for fnd in issues:
            card(fnd)
    else:
        out.append("<p>Every requirement from the RFQ that the tool can check was met, and no document disagrees "
                   "with another. See the checks that passed below.</p>")
    if obs:
        out.append(f"<details><summary>Observations ({len(obs)}): worth knowing, not failures</summary>")
        for fnd in obs:
            card(fnd)
        out.append("</details>")

    # passed
    out.append(f"<h2>3. Checks that passed ({len(res.passed)})</h2><details><summary>Show all</summary><ul>")
    for p in res.passed:
        pg = f" <span class=mut>(p. {', '.join(map(str, p.pages))})</span>" if p.pages else ""
        out.append(f"<li>{e(p.title)}{(' &mdash; <span class=mut>' + e(p.note) + '</span>') if p.note else ''}{pg}</li>")
        cited.update(p.pages[:0])
    out.append("</ul></details>")

    if res.ocr_variants:
        out.append("<h2>4. Possible misreads that were set aside</h2><details><summary>Show</summary><ul>"
                   + "".join(f"<li>{e(v)}</li>" for v in res.ocr_variants) + "</ul></details>")
    out.append("<h2>5. Only a person can confirm</h2><ul>" + "".join(f"<li>{e(h)}</li>" for h in res.human_only) + "</ul>")

    out.append(f"<h2>6. How this was produced</h2><p class=note>{e(DISCLAIMER_LONG)}</p><div class=note>"
               f"Facts by source: {', '.join(f'{k} {v}' for k, v in sorted(src_count.items()))}. "
               + (f"Handwriting was read by {facts.vision_log['model']} ({facts.vision_log['pages_read']} pages read, "
                  f"{facts.vision_log['pages_inspected']} inspected, {facts.vision_log['failed']} calls failed). "
                  if facts.vision_log.get("calls") else "") +
               "Printed text is read by OCR. Handwriting, ticks and stamps are read by a multimodal Llama model or a "
               "human transcription; a vision reading that would raise an issue is asked a second time, worded "
               "differently, and only counts as an issue if it agrees. Items marked 'manual transcription' were typed "
               "in by a person and should be spot-checked. "
               "Signature, stray-mark and highlight findings come from image analysis of the scan, which is "
               "probabilistic. Llama is used to interpret the RFQ and (when attached) read the forms; all comparisons, "
               "sums and date checks are ordinary code so they are repeatable.</div>")

    # appendix
    cited |= {ev.page for f in res.findings for ev in f.evidence if ev.box is None}
    if cited:
        out.append("<h2>Appendix: cited pages</h2><div class=pages>")
        for n in sorted(cited):
            out.append(f"<div><img alt='page {n}' src='data:image/jpeg;base64,{R.thumb(n)}'><div class=sub>Pack page {n}</div></div>")
        out.append("</div>")
    out.append("</main></body></html>")
    return "".join(out)


def to_json(req: Requirements, res: AuditResult) -> str:
    def ev(x):
        return {"page": x.page, "note": x.note, "source": x.source, "box": list(map(int, x.box)) if x.box else None}
    return json.dumps({
        "requirements": req.to_dict(),
        "findings": [{**{k: getattr(f, k) for k in ("id", "tier", "severity", "category", "title", "detail", "action", "rule")},
                      "evidence": [ev(x) for x in f.evidence]} for f in res.findings],
        "passed": [{"title": p.title, "note": p.note, "pages": p.pages} for p in res.passed],
        "ocr_variants": res.ocr_variants, "human_only": res.human_only,
    }, indent=1)
