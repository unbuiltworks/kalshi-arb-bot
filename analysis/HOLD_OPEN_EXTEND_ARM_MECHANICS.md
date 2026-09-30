# HOLD_OPEN_EXTEND — frozen arm, MECHANICS ONLY

Slice: `data/pmus_ws_stream/raw_l2/pmus_ws_20260930_1330z.jsonl.gz`,
slug `cpc-btc-updown-15m-2026-09-30-1330z`, 4,233 events / **1,282 real
prints** / **5.01 of 15.00 minutes**.

**No $/window is reported here, by construction**: the reporting script
deletes `net`, `realistic_net`, `matched_pnl`, `resid_pnl` and `fees` from
every arm's result before printing. The slice is partial and unsettled.

Arms are frozen in `src/l5_frozen_arms.py`. CONTROL and HOLD_THE_LEVEL are
byte-identical to their previous runs — unchanged, as required.

## Arm definition

A resting **OPEN/EXTEND** order keeps its exact price and its earned queue
position even when a better touch appears. A newer touch is explicitly not
a reason to move. It is given up only when it stops being a valid
open/extend (`_still_valid_open`): nothing left to fill; the side became
the *deficient* side, so the order is now a complement and must be priced
off the basis; we already hold that side and the held price no longer lowers
its aggregate economic basis; or the residual cap no longer permits the
remaining size.

## Pre-registered, NOT yet run for economics

`HOLD_OPEN_EXTEND_REACTIVE_TAKE` — same hold rule plus crossing displayed
fee-safe depth to clear residual. Registered in `ARMS` and listed in
`PRE_REGISTERED` **before** any economics exist for it. Hypothesis on the
record: holding the opening leg accumulates inventory the basis-priced
complement cannot always clear as a maker, so residual needs a taker leg
gated by the same fee-safe pair bound. No new threshold is introduced.

## Measured mechanics

`rq_oe` = OPEN/EXTEND requotes. `avoided` = requotes removed vs CONTROL.

| arm | clip | rq_oe | avoided | holds_oe | q_forfeit | q_consumed | rest_s | fills | partial | inv_up | inv_dn | adv_ord | adv_fill | max_adv |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL | 10 | 54 | 0 | 0 | 4848.83 | 5754.04 | 42 | 1 | 0 | 0 | 10 | 25 | 0 | 0 |
| CONTROL | 20 | 54 | 0 | 0 | 4848.83 | 5754.04 | 42 | 1 | 0 | 0 | 20 | 25 | 0 | 0 |
| CONTROL | 30 | 54 | 0 | 0 | 4848.83 | 5754.04 | 42 | 1 | 0 | 0 | 30 | 25 | 0 | 0 |
| CONTROL | 50 | 54 | 0 | 0 | 4848.83 | 5754.04 | 42 | 1 | 0 | 0 | 50 | 25 | 0 | 0 |
| HOLD_THE_LEVEL | 10–50 | 54 | 0 | 0 | 4848.83 | 5754.04 | 42 | 1 | 0 | — | — | 25 | 0 | 0 |
| **HOLD_OPEN_EXTEND** | 10 | **0** | **54** | 197 | 943.78 | 1784.10 | 64 | **5** | 0 | 20 | 30 | 6 | 3 | 0.03 |
| **HOLD_OPEN_EXTEND** | 20 | **0** | **54** | 161 | 452.31 | 1292.63 | 62 | **9** | 6 | 40 | 60 | 5 | 6 | 0.03 |
| **HOLD_OPEN_EXTEND** | 30 | **0** | **54** | 160 | 450.67 | 1290.99 | 62 | **11** | 8 | 60 | 90 | 5 | 7 | 0.03 |
| **HOLD_OPEN_EXTEND** | 50 | **0** | **54** | 161 | 1225.27 | 1880.59 | 72 | **8** | 6 | 50 | 100 | 12 | 8 | 0.01 |
| HOLD_OPEN_EXTEND_REACTIVE_TAKE | 10–50 | 0 | 54 | — | — | — | — | identical to HOLD_OPEN_EXTEND on this slice | | | | | | |

Requested items, in order:

1. **OPEN/EXTEND requotes avoided** — 54 of 54, at every clip. The entire
   measured churn is removed.
2. **Queue position preserved** — forfeiture falls from 4,848.83 to
   450.67–1,225.27 contracts, a **75–91% reduction**.
3. **Queue consumed before fill/cancel** — 1,290.99–1,880.59 (CONTROL
   5,754.04). CONTROL ate far more queue and converted almost none of it:
   it kept restarting the climb.
4. **Resting seconds** — 62–72 order-seconds, vs 42 for CONTROL.
5. **Maker fills** — 5–11, vs **1** for CONTROL. **Partial fills** 0–8.
6. **Inventory created** — 20/30, 40/60, 60/90, 50/100 (up/down contracts).
   CONTROL created 0/10–0/50: one leg only, never a pair.
7. **Adverse movement after placement** — yes, and it is small: 5–12 held
   orders went stale-high, 3–8 fills landed while stale-high, and the worst
   excursion was **1–3 ticks** ($0.01–$0.03). Holding did not expose the
   arm to a large adverse drift on this slice.

## Honest caveats

* `HOLD_OPEN_EXTEND_REACTIVE_TAKE` is **identical to HOLD_OPEN_EXTEND** on
  this slice: the residual never met the fee-safe crossing condition within
  5.01 minutes. That is not evidence the take is useless — it is evidence
  this slice cannot test it. It stays pre-registered.
* More fills is not more profit. Whether the inventory these fills create
  pairs profitably is exactly the question the partial slice cannot answer,
  and no net figure is offered.
* The adverse-movement measurement is over held orders only; the control
  forfeits its position before most adverse drift can accrue, so the
  columns are not a like-for-like risk comparison.

## Next step

Retrieve a complete settled print-accurate window, then run all four frozen
arms side-by-side and report full economics. No flags change between now
and then.
