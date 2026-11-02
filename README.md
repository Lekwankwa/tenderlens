# TenderLens

**Audit a bidder's tender response pack against what the buyer asked for, before it is submitted.**
TenderLens reads a South African RFQ/Tender and a bidder's response pack (scans, handwriting, ticks, stamps and
all), and reports every potential issue with the page it is on, the rule it relates to and what a person should check.
It is a review aid: it says "needs review", never "disqualified".

Powered by **Meta's Muse Spark Open** (multimodal, served through Fireworks AI): one model reads both the buyer's
requirements and the scanned pages. Everything that can be decided by ordinary code (sums, dates, identifiers matching
across documents, page counts) is decided by code, so those results are repeatable.

---

## 🚀 Quick start for judges

| | |
|---|---|
| **Live app** | https://tenderlens-366896648857.africa-south1.run.app/ |
| **Access code** | `TL-CMZK-X6NQ-8DCX-EF2S` (enter it in the sidebar) |
| **Pre-generated report** | https://tenderlens-report.netlify.app |

A full live audit of a long tender pack takes **roughly 5 minutes**. **If you don't have the time, the demo video (sent to you separately) and the
pre-generated report show exactly what the live app produces** from the sample files below.

### Try the live app (about 5 minutes)

No tender on hand? Two sample files are in this repo's [`demo/`](demo/) folder. Download them from GitHub first
(open the file, then click the download icon, or use "Download raw file"):

- [`demo/1_RFQ_Tender_1CE.pdf`](demo/1_RFQ_Tender_1CE.pdf): a real, public construction tender (the buyer's side).
- [`demo/2_Bidder_Response_Pack_tampered.pdf`](demo/2_Bidder_Response_Pack_tampered.pdf): a synthetic bidder's response
  pack (fictional company) with defects planted in it, for TenderLens to find.

Steps:

1. Open the [live app](https://tenderlens-366896648857.africa-south1.run.app/) and enter the access code
   `TL-CMZK-X6NQ-8DCX-EF2S` in the sidebar.
2. Under **1. RFQ/Tender documents**, attach the sample RFQ/Tender PDF from `demo/`.
3. Under **2. The bidder's response pack**, attach the sample response pack PDF from `demo/`.
4. Under **3. Pack stage**, press **Draft pack**.
5. Tick the box confirming you acknowledge the privacy notice.
6. Press **Audit the pack**.
7. Wait roughly **5 minutes** while the report is generated (the progress panel shows each stage). Then explore the
   verdict banner, the issues with cropped page evidence, the checks that passed, the page viewer, and the HTML/JSON
   report downloads.

You can also use your own tender and response pack (PDF, scans are fine).

---

## What it does

1. **Requirements.** The model reads the RFQ/Tender and returns a checklist. Every item must carry a verbatim
   `source_quote`; items whose quote is not in the source are dropped. A rules pass runs alongside as a cross-check.
2. **Response facts.** Scans are OCR'd (Tesseract). Handwriting, ticks, dates, amounts and stamps are read by the
   multimodal model. Every fact is tagged `ocr`, `vision` or `manual`.
3. **Audit (ordinary code).** Required documents and page counts, signatures and initials, unresolved template blanks,
   cross-document identifiers, quote arithmetic, specification match, expiry dates, declarations against CSD/CIPC/SARS
   documents, stray marks.
4. **Report.** Self-contained HTML plus JSON, with the page evidence beside each finding.

### Two tiers, so noise stays out of the headline
- **Issues:** a requirement the buyer states is not met, or two documents contradict each other.
- **Observations** (collapsed, never counted in the headline): worth knowing, not a failure.

### Guardrails against model error
- A single vision reading is never enough for an issue. Anything that would raise one is asked again as a differently
  worded question, and only a second agreeing look makes it an issue.
- Where two readings of a number differ by a character or two, both are re-read at higher magnification before anything
  is raised.
- The model reads fields; code makes the decisions.

## Tech stack
- **Model:** Meta Muse Spark Open (`accounts/fireworks/models/muse-spark-open`) via Fireworks AI's OpenAI-compatible
  API, for both text and vision.
- **App:** Python, Streamlit. Tesseract and Poppler for OCR and page rendering; OpenCV/NumPy/Pillow for image checks.
- **Hosting:** Google Cloud Run (Johannesburg).

## Measured, honestly
Benchmarked on nine public tenders paired with synthetic clean and tampered response packs (383 planted defects in
total). On the strictest scoring, a defect only counts when the finding is about that defect, TenderLens matched
**69 of the 383** planted defects, and the clean packs produced **150 issue-tier findings across 9 packs** after the
false-positive fixes (down from 285). Hand-checking puts true detection at roughly 14-18% on those packs: it
is a strong first pass for a human reviewer, not a replacement for one. Details are in
[`evaluation/ce_baseline/benchmarks/fp2/README.md`](evaluation/ce_baseline/benchmarks/fp2/README.md).

## Limits
- Signature authenticity, forged stamps and digitally edited PDFs cannot be judged.
- No live look-ups (SARS, CSD, CIPC, Treasury registers).
- Handwriting reading depends on scan quality; anything marked *manual* or *vision* should be spot-checked.
- Page images are sent to Fireworks AI (USA) so the model can read them. Upload only what you are allowed to share.
  See [`PRIVACY.md`](PRIVACY.md).

TenderLens is a review aid built with AI and fixed rules. AI can make mistakes and miss things; check anything that
matters against the original documents.
