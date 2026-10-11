# Maker lifecycle, replayed concurrently over 12 clean windows

Source: `analysis/pmus/evidence/observer_rows_all_windows.jsonl`, filtered to
rows that were NOT backlog, were still in their window at receipt, and had a
readable authenticated BBO — 569 clean rows across 12 windows of 2026-09-23,
of which 217 were already classified `MAKER_COPYABLE` by the observer.
`TRADE_FLOW` uses `analysis/pmus/evidence/auth_book_tape.jsonl`.

Every preregistered rule applied exactly: exact L5 price, post-only, no
improvement, no chasing, no maker→taker conversion, cancel only on L5
reversal or the window guard, authoritative identity.

**No fill is inferred anywhere.** A replayed order's life ends at a cancel
rule or the window guard, never at an imagined fill.

## The two regimes

| | `max_live_orders = 1` (deployed) | unbounded count, $5 cap only |
|---|---:|---:|
| maker intents | 217 | 217 |
| placed | **71** | **185** |
| peak simultaneous orders | **1** | **19** |
| **peak concurrent premium** | **$4.4198** | **$4.9983** |
| arrived while another rested | 144 | 144 |
| lost to one-live-order | **143** | **0** |
| lost to $5 envelope | **0** | **29** |
| BOOK_REACHED | 15 | 60 |
| TRADE_FLOW | 33 → 19 | 33 |
| lifetime p50 / p75 / p90 (s) | 32.0 / 64.2 / 158.2 | 53.7 / 64.2 / 158.2 |

Per-window peaks under the $5-only regime:

| window | peak orders | peak premium | intents | placed | env-blocked |
|---|---:|---:|---:|---:|---:|
| 1745z | 19 | $4.9983 | 53 | 44 | 8 |
| 1730z | 15 | $4.7826 | 38 | 36 | 2 |
| 1515z | 5 | $3.9496 | 27 | 23 | 4 |
| 1630z | 5 | $4.2273 | 19 | 17 | 1 |
| 1645z | 5 | $4.8058 | 22 | 16 | 6 |

## What this settles

**The order-count limit is the binding constraint, not the dollar cap.**
Under `max_live_orders = 1`, 143 of 217 intents were lost to the count and
**zero** to the envelope, and concurrent premium never exceeded $4.42 —
the $5 cap was never the reason anything was refused.

Remove the count and the dollar cap binds only at the edges: 19 orders can
rest simultaneously for **$4.9983**, still inside $5, and only 29 of 217
intents are excluded by money.

Worst-case exposure for a bought outcome token is the premium paid: a token
settles at 0 or 1, so a buyer loses at most what they put in. Concurrent
worst case is therefore the sum of price × quantity over everything resting
and filled — **not the count of orders**.

**What the $5 cap itself excludes: 29 of 217 intents, 13.4%.** That is the
honest cost of keeping the dollar boundary where it is, and it is small
compared with the 65.9% the count limit excludes.

## What it does not settle

The book is sampled at action instants rather than continuously, so
BOOK_REACHED is a lower bound — a touch that came and went between two
actions is not seen. TRADE_FLOW draws on a tape that covers these windows
only partially. Both are conservative in the direction that matters: they
understate executability rather than overstate it.

And neither is evidence of a fill. Across 12 windows this replay produced
**zero fills**, by construction, because nothing but the venue may produce
one. The fill rate of a resting L5-priced order is still completely unknown,
and that is the single thing the next probe exists to measure.
