# Comparison: `dpi100` (before) vs `gated5` (after the SBD gate, the inheritance limit and adaptive initials)

Same key, same packs, same image profile (100 DPI, JPEG quality 85). One run each, so differences of one or two rows are within run-to-run noise. 'Clean-pack findings' counts every finding on the CLEAN pack: a clean pack should draw none at issue tier.

| Grade | Setting | Defects | Detected | Page only | Wrong page | Missed | Clean findings (all) | Clean issue-tier | Clean 'page cut off' | Tampered findings | Vision calls (c+t) | Tokens in+out (c+t) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 (1CE) | dpi100 | 50 | 9 | 20 | 10 | 11 | 64 | 53 | 25 | 56 | 472 | 921,262 |
| 1 (1CE) | gated5 | 50 | 9 | 18 | 6 | 17 | 35 | 26 | 0 | 34 | 214 | 462,156 |
| 4 (4ce) | dpi100 | 32 | 5 | 21 | 1 | 5 | 60 | 50 | 29 | 54 | 324 | 646,052 |
| 4 (4ce) | gated5 | 32 | 8 | 20 | 1 | 3 | 21 | 19 | 0 | 25 | 158 | 342,070 |
| 8 (8CE) | dpi100 | 32 | 4 | 18 | 2 | 8 | 117 | 89 | 50 | 117 | 519 | 1,017,585 |
| 8 (8CE) | gated5 | 32 | 2 | 12 | 3 | 15 | 45 | 40 | 1 | 50 | 159 | 351,525 |

## Totals

| Setting | Defects | Detected | Missed | Clean findings | Clean issue-tier | Vision calls | Tokens |
|---|---|---|---|---|---|---|---|
| dpi100 | 114 | 18 | 24 | 241 | 192 | 1315 | 2,584,899 |
| gated5 | 114 | 19 | 35 | 101 | 85 | 531 | 1,155,751 |

## The vision-reading false positives (clean packs): page cut off + stray marks / notes

| Setting | Page cut off | Stray marks / notes | Together |
|---|---|---|---|
| dpi100 | 104 | 49 | 153 |
| gated5 | 1 | 21 | 22 |

Clean-pack findings: 241 -> 101 (-58%); issue-tier: 192 -> 85; detected defects: 18 -> 19; tokens: 2,584,899 -> 1,155,751.

## gated5: the page-cut-off fix measured through the full pipeline

Same six packs, same 100 DPI / quality 85 / 15 px border, with the `page_cutoff` running-edge and long-side fix.

| Setting | Page cut off | Stray marks / notes | Together | Clean findings (all) | Clean issue-tier | Detected (of 114) |
|---|---|---|---|---|---|---|
| gated3 | 27 | 42 | 69 | 152 | 113 | 20 |
| gated4 | 27 | 21 | 48 | 124 | 111 | 20 |
| gated5 | 1 | 21 | 22 | 101 | 85 | 19 |

The 69 became 22 (-68%). Detected defects 20 -> 19 is one row, inside run-to-run noise (one run per setting).
