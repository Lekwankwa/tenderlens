# TenderLens (prototype)

Audits a South African RFQ response pack against what the buyer asked for, and reports every
potential issue with the page it appears on, the rule it relates to, and what a person should check.
It is a review aid: it says "needs review", never "disqualified".

> This report was produced with AI (Meta's Llama 4 Scout) and fixed rules. AI can make mistakes and miss
> things. TenderLens is a review aid, not a verdict: check anything that matters against the original
> documents.

## Quick start
```
./install.sh                                # macOS / Linux.  Windows: .\install.ps1 in PowerShell
.venv/bin/python -m tenderlens ui           # starts Ollama if needed, fetches the configured model(s), opens the web app
```
`install.sh` / `install.ps1` install Tesseract, Poppler and Ollama, create the Python environment and copy `.env.example`
to `.env`. `python -m tenderlens ui` then starts Ollama if it isn't running, pulls any configured model that isn't on
disk yet, and opens the Streamlit interface. The interface's sidebar has its own **System** panel (Tesseract, Poppler,
text model, vision model, each Ready or Missing) with a button that does the same start-and-fetch without leaving the
app. `python -m tenderlens check-llm` does the same checks from the command line.

## Flow
1. **Requirements** (`requirements.py`): Meta Llama reads the RFQ (the cover email/letter and, if the buyer sent them
   as separate files, its attachments - up to 10, concatenated in order) and the spec, and returns a JSON checklist.
   Every item must carry a verbatim `source_quote`; items whose quote is not in the source are dropped.
   A rules pass runs alongside as a cross-check and as the offline fallback. Disagreements are recorded, not hidden.
2. **Response facts** (`ingest.py`, `facts.py`): scans are OCR'd (Tesseract). Handwriting and ticks are read by a
   multimodal Llama model (`vision_extract`) or, if none is attached, from a manual transcription file.
   Every fact is tagged `ocr`, `vision` or `manual`.
3. **Audit** (`audit.py`, `vision.py`): ordinary code, so results are repeatable. Checks: required documents and page
   counts, signatures, unresolved template blanks (bidder vs buyer), SBD 6.2 consistency, cross-document identifiers,
   quote arithmetic, spec match, expiry dates, declarations vs CSD/CIPC/SARS, stray marks, highlights left in.
4. **Report** (`report.py`): self-contained HTML (+ JSON). Embedded page images are redacted by default (`redact.py`).

## What counts as an issue
A pack that already won should come back clean, so the report separates two tiers:
- **Issues**: a requirement the RFQ states is not met (missing document, missing or duplicated page, unsigned form, wrong
  quantity, price or validity), or two documents contradict each other (a number, name, date or reference that differs).
- **Observations** (collapsed, never counted in the headline): context that is worth knowing but is not a failure, such as
  an expired B-BBEE entry on the CSD printout when a newer affidavit is attached, or a very small mark on a form.

The tool does not flag style (a company name where a person's name was asked, "Company" ticked instead of "(Pty) Ltd",
one decimal on a price). Those were in an early version and were noise.

When two readings of the same number differ by a character or two, the tool re-reads both at higher magnification. Two
agreeing reads settle it. Failing that, it treats the difference as a scanner misread only if the characters are ones the scan
resolution confuses (1/4, 5/8 ...) AND at least two other pages agree. Otherwise it raises an issue.

## What it checks
- **Documents and pages:** required documents present; each standard form has all its pages; blank and duplicated pages;
  pages in order (a form's pages sit together, "page n of m" footers count up, and each page's wording follows the blank
  form's order); CSD sections present and in order.
- **Typed content:** the quotation against the RFQ and specification (quantity, arithmetic, validity, reference, closing
  date, submission email, item description, delivery address); identifiers and names across documents.
- **Certificates:** CIPC status "In Business"; CSD business status, active flag, restricted flag, tax status, report age;
  SARS PIN purpose, issue and expiry; the affidavit's own content (turnover statement, level against ownership,
  recognition %, 12-month validity, commissioned date).
- **Handwriting (needs a vision model or a transcript):** every tick, name, date and amount on the quotation, SBD 4, 6.1,
  8, 9 and the affidavit: blank entries, both boxes ticked, a "Yes" where the clean answer is "No", impossible or late dates,
  forms dated on different days, a different person signing, points claimed against the B-BBEE level, years in business
  against the company's age, witness count, the commissioner's stamp and both signatures.
- **What only a vision model sees (`inspect_pages`):** crossed-out printed text, overwriting or correction fluid, handwritten
  notes outside the form fields, rotated or cut-off pages, a "signature" that is not a pen signature, and a signature that
  looks like another hand's (observation only).
- **Image analysis (no model):** signatures, initials, margin marks, leftover template highlighting.

A single vision reading is never enough for an issue. Any reading that would raise one is asked again as a differently worded
question; if the second look agrees it is an issue, otherwise it stays an observation. A person's transcript counts as
confirmed unless its confidence is under 0.8. See `OLLAMA.md`.

## Tamper tests
`python tests/tamper.py` starts from the winning pack, changes one thing at a time and checks the result. The untouched pack
must give 0 issues. The rest remove a page, change a typed figure or identifier on the quote or a supporting document, blank
a signature, add a pen scribble, remove initials, shuffle pages, change a certificate's content, or alter a handwritten
answer. Results are in `out/tamper/summary.md`.

Rows marked *(reading simulated)* change what the handwriting reader reports, not the image: they test the rules that act on
a reading, not the vision model's eyes. How well a particular model reads is measured separately with
`python -m tenderlens eval-vision`.

Handwriting transcribed by hand is tied to the page image it was read from (hash), so it follows a page that moves and is
dropped for a page that was altered: a transcription of a different image proves nothing.

## Interface (Streamlit)
```
pip install -r requirements.txt
streamlit run app.py
```
Upload the RFQ, the specification and the response pack in the sidebar, then press **Audit the pack**. The page shows a
verdict banner, the issues with cropped page evidence, what the buyer asked for, the checks that passed, a page viewer and how
each fact was read. The HTML and JSON reports download from the top right. If `samples/` holds the sample RFQ and the tamper
outputs (`python tests/tamper.py`), a "Use the sample RFQ" option lets you pick the winning pack or any tampered variant
without uploading anything, which is the safest way to demo on a slow laptop. Results are cached by page, so re-running a pack is fast.

## Run (command line)
```
sudo apt install tesseract-ocr poppler-utils   # or your platform's equivalent
pip install -r requirements.txt
python -m tenderlens run \
  --rfq samples/rfq/rfq_email.pdf --spec samples/rfq/spec_0310.pdf \
  --pack samples/response/pack.pdf --blank samples/blank \
  --transcript samples/response/pack.transcript.json \
  --bidder "Excel Supply Holdings" --out out
```
`--rfq` takes one or more PDFs (up to 10): the cover email/letter plus any attachments the buyer sent as separate
files (annexures, a BOQ, blank SBD templates). Their text is concatenated in the order given before anything is
extracted, so `--rfq email.pdf annexure_a.pdf annexure_b.pdf` works. `--spec` is its own, optional, single file -
it has its own checks (item description, quantity, delivery address) that need to know specifically which document
is the specification, so it isn't folded into the RFQ files.

## Local content
SBD 6.2 and Annexure C are only treated as required when the *buyer's* documents say local content applies (a designated
sector or a stated threshold). The RFQ's "where there is local content" wording alone does not trigger them, and neither
does a percentage the bidder wrote on a form. Override with `--local-content yes|no`.

## Using Llama
Any OpenAI-compatible endpoint works. The default in `.env.example` is **DeepInfra**, with **one model for both
steps: Llama 4 Scout** (multimodal - replaces Llama 3.2 Vision, used everywhere in this project now; text and
vision use the same base URL, model and key on purpose). Settings go in `.env` (copy `.env.example`; needs
`python-dotenv`, in `requirements-dev.txt`) or the environment; real environment variables win over the file either way:
```
export TENDERLENS_LLM_BASE_URL=https://api.deepinfra.com/v1/openai
export TENDERLENS_LLM_MODEL=meta-llama/Llama-4-Scout-17B-16E-Instruct
export TENDERLENS_LLM_API_KEY=...
export TENDERLENS_VISION_BASE_URL=https://api.deepinfra.com/v1/openai   # optional: falls back to the text model
export TENDERLENS_VISION_MODEL=meta-llama/Llama-4-Scout-17B-16E-Instruct
export TENDERLENS_VISION_API_KEY=...
```
With no endpoint set the tool runs rules-only and says so at the top of the report. Other OpenAI-compatible hosts
(Meta's own Llama API, Together, Groq, Fireworks) work the same way - just change the base URL, model name and key.
`check-llm` tests both endpoints with one tiny call each; `eval-vision` scores the vision model against a manual
transcript. Token totals (and an estimated cost, if you've set a price per million tokens) are printed by `run` and
shown in the app's "How this was read" tab.

**Local alternative: Ollama.** Self-hosting keeps bid documents on your own infrastructure, at the cost of being
much slower on ordinary laptop hardware (see "Known limits"), and Llama 4 Scout is too large to run that way - the
local path still uses Llama 3.2 Vision. Step-by-step setup, `check-llm`/`eval-vision` with Ollama, and a known Ollama
bug with `llama3.2-vision` (`unknown model architecture: 'mllama'`, not this project's bug - see
[ollama/ollama#16490](https://github.com/ollama/ollama/issues/16490)) are all in `OLLAMA.md`.

## Privacy
`samples/response` and `samples/rfq` hold real identifiers and are git-ignored. Analysis runs on the originals; images in the
report have IDs, tax numbers, bank numbers, phone numbers, PINs, handwritten ID/tax fields, the director table and
signatures blacked out. `--strict-redact` also masks registration and CSD numbers. `--no-redact` is for private use only.
Redaction is layered but not perfect: look at the report before you share it.

**With a hosted model connected (DeepInfra by default), page images are NOT redacted before being sent** - the model
has to see the handwriting, IDs and signatures to read them. That's a third party outside South Africa. The app
shows a short reminder of this near the upload boxes; self-hosting with Ollama (see `OLLAMA.md`) keeps everything
on your own machine instead.

## Pilot mode
**Off by default.** With `TENDERLENS_PILOT` unset (or anything other than `1`), the app is the plain demo: no code
box, no pack counting, the handwriting transcript upload is back. Set `TENDERLENS_PILOT=1` (locally, or as a Secret
on a host) to turn the gate back on when you're ready to run a pilot.

`TENDERLENS_PILOT=1` gates "Upload my own files" behind a one-time invite code; "Use the sample RFQ" stays free for
everyone, and the handwriting transcript upload is hidden (contractors don't get to hand-feed answers). With it unset
or `0`, the app behaves exactly as it does without this section.

```
.venv/bin/python -m tenderlens pilot create --name "Acme Builders" --email acme@example.com --packs 3 --days 21
TENDERLENS_PILOT=1 streamlit run app.py
```
`create` prints the code once (`TL-XXXX-XXXX-XXXX-XXXX`) and never stores it, only a SHA-256 hash; typed-in codes are
normalised (case, spaces and dashes don't matter, and O/I/L are read as 0/1/1) before hashing, so minor typos still work.
Five wrong codes in a row locks the sidebar for a minute. `pilot list` / `pilot show CODE` / `pilot extend CODE
[--days N] [--packs N]` (also reactivates a withdrawn code) / `pilot revoke CODE` manage existing codes.

**Counting.** A pack is spent the first time a given file's SHA-256 is audited under a code; re-running the identical
file is free, a changed file is a new pack, a crashed audit refunds the pack it had just claimed, and if two requests
race for the last pack exactly one succeeds (an atomic SQLite transaction, not a lock we hope holds).

**Storage.** Only invite details (name, optional email, pack limit, expiry), the code's hash, and per pack a file
fingerprint (SHA-256, not the file), the RFQ reference, the issue count and timestamps - never document contents,
names read from the pack, or the PDF itself. Kept in a SQLite file at `TENDERLENS_PILOT_DB` (default `pilot/pilot.db`,
git-ignored). Uploaded files and their page images are deleted from the server 24 hours after they were made.

**Hosting warning, not solved here:** if this runs somewhere that wipes its disk on restart (Streamlit Community
Cloud is the obvious one), `pilot/pilot.db` goes with it and every code's pack count resets. That's a real limitation
of running this prototype there, not something this code works around - use a host with a persistent disk (or point
`TENDERLENS_PILOT_DB` at one) if pack counts need to survive restarts.

## Known limits (be honest about these in a pitch)
- Built and tested on one pack. Thresholds in `vision.py` (stray marks, signatures) were tuned on it.
- **As of this commit, the live Llama calls against the configured vision model have not yet been run** (DeepInfra
  linking is prepared and unit-tested against a mock server; the real run against `tests/fixtures/v2/` and the
  clean pack is the next step). The vision path is tested with a scripted stand-in for the model
  (`tests/test_vision.py`), which proves the prompts' answers are parsed, cleaned, merged, double-checked and tiered
  correctly, but not how well a real model reads these scans - that's what `eval-vision` and the clean-pack run are
  for. Sample reports use rules plus a manual transcription of the handwriting.
- Tesseract misreads things (it read "5/2/1" as "5/2/4"). See the rule above; the report lists what was set aside.
- Without a vision model, a pen strike-through over printed text and scribbles in white space inside a form are not detected
  (the image rules only see margin marks). With one, they are leads that need a second confirming look.
- Altered printed wording in a standard form is only caught when it breaks a page-count, order or consistency rule; the
  tool does not compare each clause with the blank form.
- Signature authenticity, forged stamps and digitally edited PDFs cannot be judged. The signature comparison is a lead
  for a person, never proof.
- Order across different documents (for example the quotation last) is not policed; only the order inside a form is.
- A missing page is detected when the form has a known page count (SBD 4, 6.1, 6.2, 8, 9, local content declaration), when a whole
  document is missing, or when a CSD section is missing. A page in the middle of a long free-form printout (for example the
  commodity list on the CSD report) cannot be proven missing.
- Documents the RFQ does not ask for (the CIPC certificate) are not checked for presence, by design.
- No live look-ups (SARS PIN, CSD, CIPC, Treasury restricted-supplier and tender-defaulter registers), and price
  reasonableness, invitation lists and email timestamps are outside what the pack shows.

## Tests
`python -m pytest tests -q`
