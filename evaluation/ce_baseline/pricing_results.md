# Priced-schedule arithmetic: results

New check (`tenderlens/pricing.py`): the vision model reads each priced page as rows and lines, copying figures as printed; ordinary
code does the sums (quantity x rate, page totals, carry-over from page to page, VAT at 15%, escalation/contingency percentages,
section totals against the summary); a page with a discrepancy is read a second, differently worded time, and the discrepancy is an
issue only if the second reading reproduces the same figures. Otherwise it is an observation.

Scored with `tools/ce_score_pricing.py` against the key's planted arithmetic rows. A row is FOUND when a confirmed discrepancy is on
its page and shares a figure with the key's description. One run per pack. The check was tuned on grades 1, 3 and 6 only; grades 2,
9 and 7 were run once afterwards, with nothing changed.

| | Planted rows | Found and confirmed | Confirmed discrepancies on the clean packs | Other confirmed on the tampered packs |
|---|---|---|---|---|
| Development (1, 3, 6) | 13 | 6 (46%) | 5 | 8 |
| Holdout (2, 9, 7) | 16 | 11 (69%) | 7 | 10 |
| Before this check (all grades) | 29 | 0 | - | - |

## What it finds and what it cannot
- Found: a wrong quantity or a factor-of-ten amount on an item, an amount that is out by thousands on a large item, VAT calculated at
  14% instead of 15%, escalation or contingency at the wrong percentage, a total carried to the summary that is wrong, a page that
  carries a different figure from the one the next page brings forward.
- **Missed on grade 1 (all four rows): the vision model repairs figures that do not add up.** Asked to copy B1's amount, it returned
  11 970.00 (what 42 x 285.00 should be) where the page prints 11 790.00. A swapped digit looks to the reader like a misprint of the
  right answer. OCR does not recover it either: these scans are degraded, and even a 3x zoomed digits-only crop read 13 640.00 as
  1 640.00 and 11 790.00 as 4 490.00. A planted error of this kind is beyond both readers at these resolutions.
- Not covered: the key's non-schedule sums (a cost breakdown that does not add up, job-creation totals).

## False alarms
Confirmed discrepancies on clean packs: 12 across the six clean packs. They are consistent misreads (two readings agree on a wrong
figure) or a row the reader skipped. The page-sum check is skipped when the reader lists fewer rows than it says the page prints,
which removed some. Treat an issue here as a prompt to look at the page, not a proof.

## Reading times
Several earlier runs took hours (a 13,176 s pricing read, 7,960 s and 3,856 s packs). Those runs were started without the
stay-awake setting, so the Mac sleeping is a likelier cause than Fireworks; the 12-pack large run that used it had none.
