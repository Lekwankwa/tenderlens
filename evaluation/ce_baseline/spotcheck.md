# Spot-check of the scorer's matching (fp2 run)

The scorer credits a finding to a key row when the finding cites the key's page and shares wording with the key's expected finding ('detected'), or cites the page without sharing wording ('page only', not credited). Two samples were read by hand against the key's description of what was planted, to find out how much of each bucket is real.

## Sample 1: 40 of the 102 'page only' rows (stratified by grade, seed 7)

**2 of 40 were real detections (5%; 95% range about 1-17%).** Both were scored 'page only' only because the finding's wording differs from the key's:

- #26 7CE Tender T2-TC-56 (page [235]): The engine reported 'Contingency is not the stated percentage of its base' with the key's own figures (10% of 60,259,875.04 = 6,025,987.50, line 9,038,981.26). Only the wording differs from the key.
- #27 1CE T4-TC-27 (page [39]): The engine reported 'SBD 6.1: subcontracting answer is answered Yes', which is the planted answer (the further consequence on the points is not reported).

The other 38 were coincidence. 25 of them were the pack-wide finding 'Initials missing on N pages', which lists most of a pack's pages and so lands on any key page; the rest were an unrelated finding on the same page (for example a VAT error on a page where the planted defect was an unsigned Form of Offer) or nothing at all. None of the planted defects behind them (an expired licence, an insurer's indemnity below the R5m minimum, a bank rating of D, a municipal statement older than three months, a different tender named on an MBD 9) was detected.

## Sample 2: 25 of the 88 'detected' rows (seed 11)

**About 13 of 25 were real (54%; 95% range about 35-73%).** Real: VAT at 14%, escalation at the wrong percentage, an amount carried to the summary that is not there, a page total that does not add up, an item that is not quantity x rate, a blank bidder name on an MBD 9, an ID number failing its checksum, a registration number that differs between documents, two consecutive duplicate pages (twice), a missing municipal account. Coincidence: a missing second witness 'detected' by the pack-wide initials finding, an unpriced BOQ item 'detected' by a quantity x rate finding about a different item on the same page, a missing ISO certificate 'detected' by a different missing document, a swapped-pages row 'detected' by an unrelated blank question.

## Consequence for the numbers
- The old scorer overstated detection. Estimated true detection with the old matching: roughly 14% (8-21%), against the 23% reported.
- `tools/ce_score.py` now ignores pack-wide findings (initials, page counts, page cut off, stray marks, differing dates, rotated or struck pages, CSD report notes) as evidence for a key row unless the key row is itself about that thing (`--legacy` restores the old matching). Restated results (strict, 383 rows):

| Run | Old matching | Stricter matching |
|---|---|---|
| `pricing9` (before the false-positive fixes) | 88 | 71 (18.5%) |
| `fp1` | 92 | 74 (19.3%) |
| `fp2` (latest committed state) | 88 | 69 (18.0%) |

- Detection is flat across the false-positive rounds (69-74, within run-to-run noise), so the noise reduction cost no real detection.
- The stricter score still includes some coincidences of the non-generic kind (a finding about a different item on the same page), so true detection is probably 14-18%.
- Where detection is real: arithmetic (19 of 29), duplicate pages (5 of 7), missing documents (10 of 42, partly by luck of wording), blank bidder fields.

## What this means for the targets
The 85-90% detection target is far from the measured 14-18%, and the gap is mostly content-level defects (expired licences, amounts that disagree between a letter and a form, dates compared across documents, signatures and ink colour) that the current rules never look for, as well as the readers' limits on degraded scans.
