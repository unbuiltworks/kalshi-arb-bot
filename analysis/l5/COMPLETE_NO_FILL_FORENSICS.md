# Accepted-but-unfilled COMPLETEs

750 complete orders — filled 108, no_fill_cancelled 507, rejected 135 (634 carry a rest book)

## Distance from touch

| | unfilled | filled |
|---|---|---|
| orders | 404 | 105 |
| closest book gap, min (ticks) | 0 | 0 |
| closest book gap, p25 (ticks) | 1 | 1 |
| closest book gap, median (ticks) | 3.0 | 1.0 |
| closest book gap, p75 (ticks) | 8 | 2 |
| closest book gap, max (ticks) | 48 | 13 |
| share one tick away | 0.3837 | 0.5 |
| share the ask reached our price | 0.0198 | 0.1078 |
| median rest (s) | 30.67 | 7.91 |
| placed at L5's touch | 0.703 | 0.819 |

Filled completes rest a median of 1.0 tick(s) from the best opposite ask; unfilled ones rest a median of 3.0 ticks away, p75 8 and as far as 48. Distance from touch, not rest time, is what separates them: median rest 7.91s filled against 30.67s unfilled is the deadline, not a cause.

## Was a later L5-consistent action available?

| | unfilled | filled |
|---|---|---|
| L5's touch moved above our price | 164 (40.6%) | 14 |
| ...and the book reached that touch | 96 (23.8%) | 6 |

**In 164 of 404 (40.6%) unfilled completes L5's OWN touch moved above where we were sitting, and in 96 of those the opposite ask reached that new touch -- 23.8% of all unfilled completes had an L5-CONSISTENT reprice available that the book would have met. We never made it: we post once and sit to the deadline. Placement is not the defect -- we price AT L5's touch in 70.3% of them.**

Markets where the most reprices were missed:

- `aec-atp-facmen-hugdel-2026-09-06` — 21
- `aec-atp-petbru-alecan-2026-09-07` — 14
- `aec-atpcq-norgom-ollwal-2026-09-07` — 11
- `aec-cs2-misa-mouzn-2026-09-06` — 9
- `asc-cfb-smu-flst-2026-09-07-neg-3pt5` — 5
- `tsc-cfb-txs-pvam-2026-09-06-total-57pt5` — 4
- `aec-mlb-min-cws-2026-09-06` — 3
- `asc-cfb-smu-flst-2026-09-07-neg-13pt5` — 3

## This is an upper bound, not an estimate

rest_book is an AGGREGATE. min_opp_ask and max_touch are extrema over the same window and we cannot prove they coincided, so the count where both held is an UPPER BOUND on what repricing would have captured. Reaching a price is also not filling it -- we rest as maker and queue position decides -- and repricing to the touch can cross, which post-only refuses.

## Classification

BEHAVIORAL-FIDELITY FIX, not a new strategy: L5's touch moved and our clone did not follow it. Following L5's own price is cloning him; paying up to get filled would not be.
