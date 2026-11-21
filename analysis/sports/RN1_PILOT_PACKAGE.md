# RN1 replica — bounded live pilot package (DISARMED, for owner approval)

Prepared 2026-11-21 by the sports production coordinator session. Nothing here is deployed or armed.

## Release

| Item | Value |
|---|---|
| Service | `pmus-spring-l5-probe` (d84d9a68-2e5b-4f74-9694-5f422417c74f) |
| Branch / commit | `feat/pmus-rn1-replica-20261121` @ `ca8b878bb` (parent: live `62a96c5be`) |
| Code changes | RN1 replica mode (default OFF); international fair-value guard on RN1 openings; pending-cancel fill fix; per-side last-fill time |
| Tests | 19 new; sports suite: same failure set as the parent |

## Two steps, each needs your explicit approval

**Step A — disarmed release (no orders possible).** Deploy `ca8b878bb` with the RN1 settings below and
`PMUS_TWO_SIDED_SUBMISSION_LOCK=1` kept. The bot runs the replica's decisions and logs every one
(`RN1_QUOTE`); every order is refused by the lock (`place_refused_submission_lock`). Combo maker stays
`shadow`. Purpose: confirm on live books that the decisions are the intended ones before money moves.

**Step B — bounded live pilot.** After you review Step A's `RN1_QUOTE` lines: set
`PMUS_TWO_SIDED_SUBMISSION_LOCK=0` (one restart). Only this coordinator session makes either change.

## Exact settings (Step A sets all of these; Step B changes only the lock)

New (RN1 mode):

| Variable | Value | Why |
|---|---|---|
| `PMUS_TWO_SIDED_RN1` | `1` | turn the replica on |
| `PMUS_RN1_QTY` | `5` | contracts per order (RN1 median fill 155 shares; scaled down) |
| `PMUS_RN1_DEPTH_TICKS` | `1` | ESTIMATED: ticks behind the touch (not visible in RN1's fills) |
| `PMUS_RN1_PX_MIN` / `PX_MAX` | `0.20` / `0.92` | RN1 lost below 0.20; edge thin above 0.92 |
| `PMUS_RN1_PAIR_CAP` | `0.97` | completions only at a profitable pair |
| `PMUS_RN1_RESIDUAL_CAP_USD` | `3` | max unpaired cost per market |
| `PMUS_RN1_PER_MARKET_USD` | `8` | max total spend per market |
| `PMUS_RN1_EXTEND_MAX_UP` | `0.02` | an extension never pays more than avg + 2c |
| `PMUS_RN1_REFILL_COOLDOWN_S` | `60` | RN1 same-side refill gap p25 54 s |
| `PMUS_RN1_REPRICE_TICKS` | `1` | re-set a resting bid 1+ tick off target |

Changed existing settings:

| Variable | From | To | Why |
|---|---|---|---|
| `PMUS_TWO_SIDED_MAX_MARKETS` | 16 | `3` | owner: 2–5 markets |
| `PMUS_TWO_SIDED_REWARD_FIRST` | on | `0` | pick liquid in-game markets, not reward programs |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | dota2,setkamecz,setkamemd,utr,valorant,wtt | `cs2,nba,nfl,cfb,wta` | RN1's large, non-negative sports that we can trade; the gate's own history is from the old strategy |

Unchanged (existing capital and loss limits): `PMUS_TWO_SIDED_DAILY_LOSS_USD=5`, `PMUS_TWO_SIDED_TOTAL_USD=60`,
`PMUS_TWO_SIDED_OPEN_EV_GATE=1`, taker off, `PMUS_TWO_SIDED_ACCOUNT_RISK=observe`, `L5_ACCOUNT_MAX_EXPOSURE_USD=20`,
`PMUS_COMBO_MAKER=shadow`, `PMUS_TWO_SIDED_INTL_GUARD=1` (now also guards RN1 openings), watchdog as deployed.

## Worst case, by construction

- Per market: at most $8 spent, at most $3 unpaired. 3 markets → at most $24 spent, $9 unpaired at once.
- A day: openings stop at −$5 mark-to-market (`DAILY_LOSS_USD`); completions continue.
- Every order is post-only BUY. Nothing sells, nothing takes. Positions are held to settlement.

## Pilot stop rules (coordinator re-locks immediately, no automatic restart)

1. Cumulative venue-settled net P&L of pilot markets ≤ −$10.
2. Any fill the book and the venue disagree on (`INVENTORY_RECONCILE` mismatch).
3. Any unexplained order or production change.
4. 7 days elapsed: report and stop for your decision.

## Success criterion

Positive **venue-confirmed settled net P&L** on pilot markets, after fees, at our actual sizes. Rewards are reported
separately and only when credited. Trade count is not a criterion.

## What is replicated and what is not

Replicated from RN1's observable behaviour: maker bids on both outcomes, buy-only, replenish after fills,
accumulate the heavier side, patient completion that rests deeper until the pair pays, bounded unmatched
inventory, hold to settlement, repricing with the touch.

Deliberately different, per your instruction to favour profitable completion: RN1 completed at any cost (median
0.995; its completions ≥ 1.04 lost $1.52M). The replica completes only at ≤ 0.97.

Not knowable from RN1's fills: its resting depth and size, so `DEPTH_TICKS` and `QTY` are settings.

## Known risk to state plainly

RN1's own small positions lost money: conditions under $100 total lost 10.8% of dollars (−$38k over 30 days).
Our pilot's positions are all in that size range. The pilot measures whether the decision logic survives at our
size; it may not.
