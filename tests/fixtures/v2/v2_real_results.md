# Tampered pack v2: real results (DeepInfra, Llama 4 Scout)

Scored against `tests/fixtures/v2/tampered_v2_answer_key.md`, not the simulated "Run 2" in that file. Model
for both text and vision: `meta-llama/Llama-4-Scout-17B-16E-Instruct`.

**Two runs are compared:**
- **Before** (`out/v2_vision`): the first real run, before any of the fixes in `FIX_ALL_PROMPT.md`.
- **After** (`out/v2_phase2`): the current run, with Phase 1 (closing-date rule, identifier reference
  logic, stray-note image corroboration, names tiering, disclaimers) and Phase 2 (witness last-page fix,
  2.7/2.8/2.9 observation cap, handwritten-identifier-mismatch observation cap; the crop-and-reread work
  itself is **off** — `CROP_VERIFY_ENABLED = False` in `facts.py` — because it could not reliably locate a
  field's printed label on a real, filled-in scan; see the commit history for why).

Commands:
```
python -m tenderlens run --rfq samples/rfq/rfq_email.pdf --spec samples/rfq/spec_0310.pdf \
    --pack tests/fixtures/v2/tampered_pack_v2.pdf --blank samples/blank --out out/v2_phase2
```
`eval-vision` was not run (no `samples/response/pack.transcript.json`, never provided). The
missing-documents companion pack was not run (file never located or provided) — both flagged again at the
end, unchanged from before.

**Keep in mind:** a single run's readings vary call to call (the clean pack's own two runs this round
showed a witness-count finding appear and disappear, several "handwritten note" pages shift, and the stray
B-BBEE stamp/signature finding present in the first v2 run was simply absent in this one). Several row
changes below are run-to-run variance, not caused by any fix, and are labelled as such — real stability
needs two runs of the same pack compared, which is why a second clean-pack run is on the later part of the
plan, not folded into this one.

## A. Main pack — row by row, before and after

Legend: **issue** · **obs** (observation-only) · **missed** · **wrong page** (the right kind of
discrepancy was raised, but the evidence names a different page than the one this row changed) · **n/a**
(gap row, correctly not raised).

| # | What was changed | Group | Before | After | Why it changed |
|---|---|---|---|---|---|
| 1 | Quantity 30→20 | Rules | issue | issue | — |
| 2 | Validity 60→20 days | Rules | issue | issue | — |
| 3 | RFQ reference → 0311 | Rules | issue | issue | — |
| 4 | Addressed to Tenders@... | Rules | issue | issue | — |
| 5 | Delivery city → Polokwane | Rules | issue | issue | — |
| 6 | Battery 105AH→100AH | Rules | issue | issue | — |
| 7 | Closing date on quote → 2021/10/22 | Rules | **missed** | **issue** | Step 1: the OCR regex now tolerates the underscore Tesseract reads in place of the ruled line under "Closing date". |
| 8 | CSD number on quote (…851→…951) | Rules | wrong page | wrong page | Unchanged — the finding fires but its evidence (p26, printed, correct) is the majority this run too; the altered quote page (p1) is still the "agreeing" side, not flagged. Step 2 fixed vision-vs-print ties, not this printed-vs-printed one. |
| 9 | Signature removed, quotation | Rules | issue | issue | — |
| 10 | Registration number → …752… (affidavit) | Rules | issue | **obs** | The mismatch is still correctly found at p25, but a handwriting-sourced identifier mismatch is now capped at observation (this round's fix) rather than issue. |
| 11 | SA ID → …097 (affidavit) | Rules | missed | missed | Unchanged — the affidavit's own (correct, OCR) reading still loses an OCR-vs-OCR tie against the CIPC page, so p25 never appears in the evidence. |
| 12 | Company name → EXCELLENT SUPPLIES (affidavit) | Rules | issue | issue | `names()` wasn't changed this round. |
| 13 | Tax reference → …178 (SARS page) | Rules | wrong page | wrong page | Unchanged, same OCR-tie pattern as row 8. |
| 14 | Signature removed, SBD 9 | Rules | issue | issue | — |
| 15 | Pen scribble, SBD 4 p2 | Rules | issue | issue | — |
| 16 | Initials removed, 6 pages | Rules | issue | issue | — |
| 17 | CSD tax status → Non-Compliant | Rules | issue | issue | — |
| 18 | CSD Restricted Supplier → Yes | Rules | issue | issue | — |
| 19 | Affidavit level One→Three | Rules | issue | issue | — |
| 20 | CSD report dated 27 Mar | Rules | issue | issue | — |
| 21 | SARS page names 'Excel Trading Holdings' | Rules | issue | issue | Same finding as row 12. |
| 22 | SARS PIN purpose → Other | Rules | issue | issue | — |
| 23 | Affidavit income "exceeded R10m" | Rules | issue | issue | — |
| 24 | Unit price R2410.8→R2310.8 | Rules | issue | issue | Caught via the quote-arithmetic mismatch, as before. |
| 25 | SBD 4 q2.7 NO→YES | Vision | issue | issue | q2.7 is not in the observation-capped set (only 2.7.2, 2.8, 2.9 are). |
| 26 | SBD 4 q2.9 both marked | Vision | issue | **obs** | 2.9 is one of the three questions this round's fix caps at observation (no crop coverage; a confirmed whole-page reading of a small mark wasn't shown to be reliable). |
| 27 | SBD 8 q4.3 YES | Vision | issue | issue | — |
| 28 | SBD 8 date wiped | Vision | obs | obs | — |
| 29 | SBD 9 dated after closing | Vision | obs | obs | — |
| 30 | SBD 6.1 years in business 1→6 | Vision | obs | obs | — |
| 31 | Quotation signed by Thabo Mokoena | Vision | issue | **obs** | Step 3C: a "person who signed differs" finding is only an issue when every name in it is OCR; here the quote's name is a vision reading. |
| 32 | Commissioner's stamp + signature removed | Vision | issue + obs | **missed** | Not raised at all this run (neither the stamp nor the signature check fired) — this looks like run-to-run model variance, not a fix effect; it was found in the earlier run. |
| 33 | Pen line through printed paragraph, SBD 8 | Vision | issue | issue | — |
| 34 | Correction fluid over 'N/A', SBD 8 | Vision | missed | missed | — |
| 35 | Handwritten note 'price to be confirmed later' | Vision | issue | issue | Now corroborated by the non-AI image check (Step 3A) rather than the model's own repeated self-agreement — same outcome, more defensible mechanism. |
| 36 | SBD 6.1 subcontracting → YES | Vision | issue | **obs** | Run-to-run variance: the whole-page second look didn't confirm "Yes" this time. Not a fix effect. |
| 37 | SBD 6.1 points claimed 10 vs level | Vision | obs | obs | — |
| 38 | SBD 6.1 registration number → …752… | Vision | issue | **obs** | Same identifier-observation cap as row 10; still correctly found at p7. |
| 39 | SBD 6.1 company type (gap) | Vision, gap | n/a | n/a | Correctly not raised. |
| 40 | SBD 6.1 second witness removed | Vision | issue (wrong page, p3/p6, read as 0) | **issue (correct page, p8, read as 1)** | The last-page merge fix (Phase 2): a witness count can no longer be attributed to a page that only mistakenly answered the question. Now both the page and the count look right. |
| 41 | SBD 6.1 dated vs. other forms | Vision | obs | obs | Same caveat as before (SBD 4's own date in this finding doesn't match the row's premise — likely a vision misread of which form differs). |
| 42 | SBD 6.2 imported-content YES vs R0 | Vision | missed | **obs** | Now raised (as an observation) — not a change I made; the whole-page reading simply picked it up this run. |
| 43 | Local content bid price mismatch | Vision | missed | missed | — |
| 44 | Local content % mismatch | Vision | missed | missed | — |
| 45 | Local content witnesses | Vision | missed | missed | — |
| 46 | SBD 4 ID …078 | Vision | issue | **obs** | Same identifier-observation cap as row 10; still correctly found at p16. |
| 47 | SBD 4 tax reference …178 | Vision | wrong page | wrong page | Unchanged, same OCR-tie pattern as rows 8 and 13. |
| 48 | SBD 4 q2.10 neither marked | Vision | missed (misread as "Yes") | missed (same) | Still misread as "Yes" rather than blank. |
| 49 | SBD 4 q2.11 YES | Vision | issue | issue | — |
| 50 | SBD 4 dated vs. others | Vision | obs | obs | Same finding and caveat as row 41. |
| 51 | SBD 4 signature replaced | Vision | obs | obs | — |
| 52 | SBD 8 q4.1 YES | Vision | issue | issue | — |
| 53 | SBD 8 q4.2 YES | Vision | issue | issue | — |
| 54 | SBD 8 q4.4 YES | Vision | issue | issue | — |
| 55 | SBD 8 declarant's name wiped | Vision | obs | **issue** | Run-to-run variance: the whole-page second look confirmed "blank" this time. Not a fix effect. |
| 56 | Page scanned upside down, SBD 6.2 | Vision | issue | issue | — |
| 57 | Bottom quarter cut off, Local content decl. | Vision | missed | missed | The non-AI cut-off check (Step 4.5) exists but didn't fire for this page — not re-investigated in depth this round. |
| 58 | SBD 4 first two pages swapped | Rules | issue | issue | — |
| 59 | CSD report middle pages swapped | Rules | issue | issue | — |
| 60 | Blank sheet inserted | Rules | issue | issue | — |
| 61 | Page copied twice (SBD 6.1) | Rules | issue | issue | — |
| 62 | SBD 9 second page removed | Rules | issue | issue | — |

### Group totals (counted directly from the row table above)

| Group | Rows | issue | obs | missed | wrong page | n/a |
|---|---|---|---|---|---|---|
| Rules (text), 29 rows | 1–24, 58–62 | 25 | 1 (row 10) | 1 (row 11) | 2 (rows 8, 13) | 0 |
| Vision model, 32 rows | 25–38, 40–57 | 11 | 13 | 7 (rows 32, 34, 43, 44, 45, 48, 57) | 1 (row 47) | 0 |
| Gap, 1 row | 39 | — | — | — | — | 1 |

Both group rows sum to their row count (29 and 32); 29 + 32 + 1 = 62. ✓

**What the identifier-observation cap actually cost:** rows 10, 38 and 46 were clean, correctly-paged
catches that are now observations instead of issues (row 11 and 47 were never clean catches regardless —
they're the OCR-tie "wrong page" problem, unrelated to this cap). That's 3 of 32 vision-group rows moved
from issue to observation specifically by this round's reliability trade-off, same as flagged before running
it.

## False alarms (issues/observations matching no answer-key row)

One to flag, **unclear rather than confirmed**: "Entry overwritten or covered on SBD 4 (page 16)" (issue).
Page 16 is where the tampered ID and tax-reference digits sit (rows 46, 47), so this may be the image
corroboration check picking up the same digital alteration from a different angle, rather than an unrelated
false alarm — I can't tell which without a closer look at the page image, so I'm not scoring it as a clean
false alarm or a clean (unlisted) detection.

## Call counts, tokens, and cost

| Run | Text calls | Vision calls | Prompt tokens | Completion tokens | Est. cost |
|---|---|---|---|---|---|
| `out/v2_phase2` (this run) | 1 | 82 | 202,230 | 3,499 | not shown — no price set |

No call failed. Running time was not captured separately with `time` for this run (`time` was prefixed on
the command but the output was captured via `tee` and the wall-clock line wasn't retained) — not re-run
solely to capture it.

## What I still could not verify

- `eval-vision` (no `samples/response/pack.transcript.json`).
- `tampered_pack_v2_missing_documents.pdf` (never located or provided; Section B of the answer key, 8
  rows, all Rules-text, unscored).
- The one "unclear" false-alarm candidate above.
- Whether rows 32, 36, 55's changes are genuinely stable or one-off — needs the planned second clean-pack
  (and ideally second v2) run to separate real effects from call-to-call noise.
