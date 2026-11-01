# Benchmark point: nine grades with the priced-schedule arithmetic check (run tag `pricing9`)

Engine at commit `14d0b91` (priced-schedule check on). Every pack at 100 DPI, JPEG quality 85, 15 px border; Fireworks AI
`muse-spark-open`; one run per pack, so a difference of a row or two is within run-to-run noise. All 18 packs completed, with no
failed pages and no unresolved slices.

Contents: `<grade folder>/<clean|tampered>/{report.json,run.json}` for each of the 18 packs, and `scores.json` (each planted defect
scored against the verified key). To re-score: copy a pack's folder under `evaluation/ce_baseline/runs/<folder>__pricing9/` and run
`python tools/ce_score.py --tag pricing9`.

| | Baseline (`90674eb`, no pricing check) | This benchmark |
|---|---|---|
| Planted defects detected (of 383) | 68 (18%) | 88 (23%) |
| Arithmetic rows detected (of 29) | 0 | 19 (page only 8, wrong page 1, missed 1) |
| Clean-pack findings (9 clean packs) | 327 | 338 |
| Clean-pack issue-tier findings | 276 | 285 |
| Arithmetic issues on clean packs | 0 | 11 (grade 6: 4, grade 7: 5, grade 9: 2) |
| Tokens, 18 packs | 3.77M | 4.38M |
| Largest pack (454 pages), clean | 759 s | 943 s |

| Grade | Defects | Detected | Page only | Wrong page | Missed | Clean findings | Clean issue-tier |
|---|---|---|---|---|---|---|---|
| 1 | 50 | 11 | 21 | 5 | 13 | 31 | 25 |
| 2 | 39 | 11 | 23 | 1 | 4 | 11 | 9 |
| 3 | 32 | 7 | 9 | 3 | 13 | 32 | 28 |
| 4 | 32 | 6 | 20 | 1 | 5 | 22 | 20 |
| 5 | 37 | 5 | 12 | 1 | 19 | 46 | 42 |
| 6 | 60 | 21 | 16 | 12 | 11 | 65 | 57 |
| 7 | 58 | 18 | 19 | 11 | 10 | 58 | 41 |
| 8 | 32 | 2 | 12 | 3 | 15 | 46 | 40 |
| 9 | 43 | 7 | 12 | 5 | 19 | 27 | 23 |
| **all** | **383** | **88** | 144 | 42 | 109 | **338** | **285** |

The non-arithmetic detections are unchanged within noise (68 before, 69 now). `scores.json` contains text derived from the answer
keys; keep it out of anything shared outside the project.
