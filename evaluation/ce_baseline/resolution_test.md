# Resolution and reasoning test (tampered packs, grades 2 and 1)

The CE packs are scans embedded as **150 PPI JPEGs**, so 150 DPI is their native resolution: rendering at 200 DPI only interpolates the same
pixels, and cannot reveal anything the 150 PPI image does not hold. Settings compared (all JPEG quality 85, one run each):
100 DPI (the model copy used until now), native 150 (page sent unshrunk), 200 (upsampled control), native 150 with `reasoning_effort=high`.

## Grade 2 (35 pages; planted arithmetic rows T5-TC-29..32 on page 21)
| Setting | Rows detected (of 39, strict) | Arithmetic rows found | Tokens | Seconds |
|---|---|---|---|---|
| 100 DPI | 11 | 3 of 4 | 67k | 74 |
| native 150 | 9 | 2 of 4 | 99k | 77 |
| 200 (upsampled) | 9 | 2 of 4 | 127k | 80 |
| 150 + high reasoning | 12 | 3 of 4 | 143k | 202 |
No setting is better outside run-to-run noise (the one row that moves, T5-TC-31, flips between runs). 200 DPI gives nothing, as expected.

## Grade 1 (131 pages; the BOQ page 125 holds the digit-swap rows that earlier readings "repaired")
| Setting | Rows detected (of 50) | BOQ rows found (real) | Tokens | Seconds |
|---|---|---|---|---|
| 100 DPI | 7 | T4-TC-40 | 147k | 92 |
| native 150 | 12 | T4-TC-40, **T4-TC-38 (decimal shift on item C2: 0.3 x 1 250.00 = 375.00, written 3,750.00)** | 225k | 90 |
| 150 + high reasoning | 9 | T4-TC-40, T4-TC-38 (both now confirmed by the second reading) | 294k | 192 |
- At 100 DPI the model never saw the decimal point of item C2; at native 150 it did. So part of the earlier "perception limit" was our downscale.
- **T4-TC-37 (B1: 11 790.00 written, 11 970.00 correct) is still missed at every setting**, including native 150 with high reasoning. Its apparent
  credit in the 150 runs is a scorer coincidence (a finding about a different item on the same page). This is the repair bias: the model reports the
  figure that makes the line add up, not the one printed. More pixels and more reasoning do not remove it.
- T4-TC-41 (VAT written as 14%) is still only a page hit.

## Conclusions
1. Resolution matters for small marks (a decimal point), not for the repair bias; 200 DPI is pointless on 150 PPI scans.
2. High reasoning doubles the time and tokens and only turns some observations into confirmed issues; it found nothing new.
3. Sending the pricing pages at native resolution is cheap (a few dozen pages per pack, about +3k tokens each) and recovered one real row; the rest of the
   pack does not need it. Not implemented yet.
4. The repair bias needs a different mechanism: read each amount in isolation (a column or a single cell, so there is no quantity x rate for the
   model to make agree) and let the code do the sums.
