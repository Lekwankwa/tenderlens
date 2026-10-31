# CE baseline: all nine grades, final local run

Engine state: commit `fdd160d` plus the names-rule fix. Every pack at 100 DPI, JPEG quality 85, 15 px border; Fireworks AI `muse-spark-open` for every call (no fallback lane exists). Control grades 1, 4, 8 are run `gated5`; the others are run `large`. One run per pack, so a difference of a row or two is within noise. Strict detection: a finding on the key's page that also shares wording with the expected finding; 'page only' (right page, unrelated wording) is NOT credited.

| Grade | Folder | Run | Defects | Detected | Page only | Wrong page | Missed | Clean-pack findings | Clean issue-tier | Seconds (clean / tampered) | Tokens (c+t) | Timeouts (retried) | Slice errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1CE | gated5 | 50 | 9 | 18 | 6 | 17 | 35 | 26 | 519 / 741 | 462,156 | 0 | 0 |
| 2 | 2CE | large | 39 | 9 | 25 | 1 | 4 | 13 | 10 | 71 / 71 | 128,450 | 0 | 0 |
| 3 | 3CE Tender | large | 32 | 4 | 8 | 2 | 18 | 31 | 29 | 350 / 250 | 352,750 | 3 | 0 |
| 4 | 4ce | gated5 | 32 | 8 | 20 | 1 | 3 | 21 | 19 | 183 / 264 | 342,070 | 0 | 0 |
| 5 | 5CE | large | 37 | 5 | 12 | 3 | 17 | 46 | 42 | 357 / 301 | 328,122 | 3 | 0 |
| 6 | 6 CE Tender | large | 60 | 12 | 22 | 10 | 16 | 59 | 53 | 481 / 479 | 563,676 | 1 | 0 |
| 7 | 7CE Tender | large | 58 | 14 | 19 | 10 | 15 | 50 | 36 | 759 / 600 | 674,772 | 7 | 0 |
| 8 | 8CE | gated5 | 32 | 2 | 12 | 3 | 15 | 45 | 40 | 220 / 247 | 351,525 | 0 | 0 |
| 9 | 9 CE | large | 43 | 5 | 9 | 4 | 25 | 27 | 21 | 412 / 285 | 568,866 | 0 | 0 |
| **all** | | | **383** | **68** | 145 | 40 | 130 | **327** | 276 | | 3,772,387 | | |

Strictly detected: 68 of 383 planted defects (18%). Clean packs draw 327 findings in all (276 at issue tier) - a clean pack should draw none at issue tier.

## By defect class (all nine grades)

| Class | Detected | Page only | Wrong page | Missed |
|---|---|---|---|---|
| arithmetic | 0 | 11 | 1 | 17 |
| authority | 2 | 1 | 0 | 1 |
| blank | 11 | 32 | 4 | 30 |
| claim | 5 | 6 | 3 | 0 |
| correction | 1 | 1 | 1 | 2 |
| date | 8 | 4 | 7 | 12 |
| declaration | 6 | 8 | 0 | 4 |
| duplicate | 5 | 0 | 0 | 2 |
| expired | 0 | 1 | 0 | 0 |
| format | 0 | 1 | 0 | 0 |
| ink | 0 | 5 | 0 | 1 |
| invalid | 5 | 37 | 7 | 14 |
| mismatch | 8 | 35 | 16 | 17 |
| missing | 13 | 0 | 0 | 29 |
| not relevant | 0 | 1 | 0 | 0 |
| order | 1 | 0 | 1 | 0 |
| page order | 3 | 2 | 0 | 0 |
| stale | 0 | 0 | 0 | 1 |

## What the numbers say

- **Arithmetic and pricing errors are not checked on these tenders**: 0 of 29 detected strictly (11 had a finding on the right page with unrelated wording); bill of quantities and Form of Offer totals are not checked.
- **Missing documents**: 13 of 42 detected. The engine finds a missing document only when the tender's requirement list names it and no page shows it.
- **Duplicate pages**: 5 of 7 (adjacent near-copies are found; a duplicate whose OCR differs much is not).
- **Dates and form answers** are the strongest classes; **invalid certificates, mismatches and blanks** are mostly 'page only': the engine flags something on the page but not what the key describes.
- Processing: all 18 packs ran, including 450-page packs (759 s, 712 MB peak); 13 timeouts in the large run were retried and recovered; no slice errors.
- Remaining false positives on clean packs are mostly signature and stamp calls, cross-document mismatches, crossed-out text and stray marks.

Nothing here shows the model was trained or improved; it is a measured baseline of the existing engine.
