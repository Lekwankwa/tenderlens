# Benchmark point: nine grades after the false-positive fixes (run tag `fp2`)

Engine: the working tree committed together with this folder (classification that checks a page still looks like its form, identity facts from
the bidder's own pages only, a signature threshold of 150, voided/struck-through forms, the EME-only income rule). Every pack at 100 DPI, JPEG quality
85, 15 px border; Fireworks AI `muse-spark-open`; one run per pack. All 18 packs completed.

`scores.json` uses the **stricter matching** (pack-wide findings no longer count as evidence for a key row unless the row is about that; see
`../../spotcheck.md`). The earlier benchmark's scores under the same matching are in `../pricing9/scores_stricter_matching.json`.

| | `pricing9` | `fp2` |
|---|---|---|
| Clean-pack findings, all (9 clean packs) | 338 | 220 |
| Clean-pack issue-tier findings | 285 | 150 (-47%) |
| Signature and stamp / cross-document / unexplained mark | 42 / 27 / 93 | 12 / 2 / 35 |
| Planted defects detected, stricter matching (of 383) | 71 | 69 |
| Planted defects detected, old matching | 88 | 88 |

Detection did not drop: the noise reduction removed false positives, not real detections (69 against 71, within run-to-run noise). The hand-checked
estimate of true detection is 14-18% (see `../../spotcheck.md`).
