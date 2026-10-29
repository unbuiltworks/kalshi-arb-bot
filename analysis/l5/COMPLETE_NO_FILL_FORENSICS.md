# Accepted-but-unfilled COMPLETEs

868 complete orders — filled 125, no_fill_cancelled 581, rejected 162 (749 carry a rest book)

## Distance from touch

| | unfilled | filled |
|---|---|---|
| orders | 475 | 122 |
| closest book gap, min (ticks) | 0 | 0 |
| closest book gap, p25 (ticks) | 1 | 1 |
| closest book gap, median (ticks) | 2 | 1 |
| closest book gap, p75 (ticks) | 7 | 2 |
| closest book gap, max (ticks) | 48 | 13 |
| share one tick away | 0.4274 | 0.5299 |
| share the ask reached our price | 0.0168 | 0.094 |
| median rest (s) | 6.99 | 7.71 |
| placed at L5's touch | 0.7474 | 0.8443 |

Filled completes rest a median of 1 tick(s) from the best opposite ask; unfilled ones rest a median of 2 ticks away, p75 7 and as far as 48. Distance from touch, not rest time, is what separates them: median rest 7.71s filled against 6.99s unfilled is the deadline, not a cause.

## Was a later L5-consistent action available?

| | unfilled | filled |
|---|---|---|
| L5's touch moved above our price | 176 (37.0%) | 15 |
| ...and the book reached that touch | 106 (22.3%) | 7 |

**In 176 of 475 (37.0%) unfilled completes L5's OWN touch moved above where we were sitting, and in 106 of those the opposite ask reached that new touch -- 22.3% of all unfilled completes had an L5-CONSISTENT reprice available that the book would have met. We never made it, because the touch_moved re-quote is gated to OPEN and EXTEND: 0 of 868 COMPLETEs ever exited that way, against 36 OPEN and 35 EXTEND. Placement is not the defect -- we price AT L5's touch in 74.7% of them.**

Markets where the most reprices were missed:

- `aec-atp-facmen-hugdel-2026-09-06` — 21
- `aec-atp-petbru-alecan-2026-09-07` — 14
- `aec-atpcq-norgom-ollwal-2026-09-07` — 11
- `aec-cs2-misa-mouzn-2026-09-06` — 9
- `asc-cfb-smu-flst-2026-09-07-neg-3pt5` — 5
- `tsc-cfb-txs-pvam-2026-09-06-total-57pt5` — 4
- `aec-wta-martre-jenrug-2026-09-08` — 3
- `aec-atp-vilgau-pavlag-2026-09-08` — 3

## This is an upper bound, not an estimate

rest_book is an AGGREGATE. min_opp_ask and max_touch are extrema over the same window and we cannot prove they coincided, so the count where both held is an UPPER BOUND on what repricing would have captured. Reaching a price is also not filling it -- we rest as maker and queue position decides -- and repricing to the touch can cross, which post-only refuses.

## Classification

BEHAVIORAL-FIDELITY FIX, not a new strategy: L5's touch moved and our clone did not follow it. Following L5's own price is cloning him; paying up to get filled would not be.
