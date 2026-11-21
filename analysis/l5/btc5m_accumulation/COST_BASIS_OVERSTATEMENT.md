# The account halt counts our 8c legs as $1.00 legs

Found 2026-11-13 by asking the BTC15m session to read `l5-pmus-live`'s
heartbeat; confirmed against this lane's own venue-confirmed fills. It is
the reason every BTC5m window after 1620z was refused, and it will recur
every day that BTC5m trades.

## The mechanism

`l5-pmus-live` publishes the account-wide daily realized figure that
BTC5m's gate reads. It can SEE our BTC5m positions but has no cost basis
for them, so it books them under provenance `UNKNOWN_COST_UPPER_BOUND`:
every leg valued at **$1.00 a share**.

BTC5m deliberately rests at **0.08**. So every losing leg this strategy
opens is booked at **12.5x what it actually cost**.

The bound is not a bug. It is refusing to assume a cost it does not know,
which is the correct conservative behaviour for an account-wide loss
ceiling. The defect is that nothing hands it our basis.

## 2026-11-13, measured

| market | booked by l5-pmus-live | our venue-confirmed actual | overstated |
|---|---|---|---|
| 0035z | -28.12 | **-2.2496** | 25.8704 |
| 1605z | 0E-8 | **+4.2000** | 4.2000 |
| 1610z | -5.00 | **-0.4000** | 4.6000 |
| 1615z | -5.00 | **-0.4000** | 4.6000 |
| **total** | **-38.12** | **+1.1504** | **39.2704** |

Fills, with venue order ids:

```
0035z  UP   28.12 @ 0.08  DPCNDNYG14F0   cost 2.2496  settled -2.2496
1605z  UP    5.00 @ 0.08  DPSZ4S3013ZK   cost 0.80    settled +4.2000
       DOWN  5.00 @ 0.08  DPSZ4WG014EX   (pair, redeemed 1.00/share)
1610z  UP    5.00 @ 0.08  DPT1RZAG14F1   cost 0.40    settled -0.4000
1615z  UP    5.00 @ 0.08  DPT427WG13Z6   cost 0.40    settled -0.4000
```

`1610z + 1615z = exactly -10.00`, which is the clean -10.00000000 step
that appeared between the 14:31Z and 15:51Z deployments and started this.
0035z is the same mechanism and 11x larger: 28.12 contracts at an assumed
$1 instead of 28.12 shares at 0.08.

**Those four markets did not lose 38.12. They made +1.15.** 1605z is the
clearest case: a completed pair that redeemed at 1.00/share for a real
+4.20 gain, booked as zero.

## What it cost us

Published day figure **-39.5296** of a **-40.00** halt, leaving
**0.4704** of room against a **1.00** exposure ceiling -- so the gate
refused every window with `EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM`.

Corrected, the day is about **-0.26** and the room about **39.74**.

**39.27 of a 40.00 daily halt was consumed by 2.25 of actual risk.**

Every independent signal already disagreed with the upper-bounded figure:
`unattributed_usd` 0, `available_cash_usd` ROSE 339.79 -> 343.19 over the
interval that booked -10.00, `portfolio_drawdown` reads 6.51 ABOVE its
336.68 anchor, and `verified_l5_realized_usd` is -3.8076. Only the figure
that gates trading was wrong, and it was wrong by 39.27.

## Why this blocks the 100-window plan, not just today

This is not a one-off. At 5 shares a side, a single-fill window books
**-5.00** instead of **-0.40**, and a 13-campaign run is ~100 windows.
At the tape's ~87% single-fill rate that is roughly 87 losing legs a run,
booked at $1 a share: on the order of **-435 against a -40 daily halt**,
on real risk of about -35.

So the account halts on phantom losses within the first few campaigns of
EVERY day BTC5m runs. Waiting for 00:00Z works once; it does not work
thirteen times.

## The fix direction

Give `l5-pmus-live` our cost basis so the bound stops applying -- not
loosen the bound, and not raise the halt. The bound is the only thing
standing between an unknown-cost position and an unbounded assumption,
and BTC5m's whole economics depend on legs costing 8c rather than 1.00,
so the basis is exactly the missing input.

Candidates, none implemented, all needing owner and BTC15m agreement:

1. BTC5m publishes its settled per-market basis where l5-pmus-live
   already reads (it publishes a risk doc on :8081; the reverse channel
   does not exist).
2. `l5-pmus-live` attests BTC5m markets from the venue's own fill history
   rather than assuming $1 (it has the credentials; the fills are real
   and venue-confirmed, as the order ids above show).
3. Scope the account-wide daily halt so a lane's own settled results are
   read from that lane's ledger. Note `L5_LOSS_GATE_RESCOPING_REVIEW.md`
   already considered and REJECTED population-narrowing, because it would
   leave manual trading with no ceiling. Any version of this has to keep
   manual trading covered.

## Do not

- Do not raise `L5_DAILY_REALIZED_LOSS_HALT_USD` to get around it. The
  halt is correct; its input is wrong.
- Do not edit `retired_markets` to zero the day. That is falsifying
  settlement history, and the rows are the evidence.
- Do not read a corrected figure as licence to trade bigger. Correcting
  an overstatement restores the envelope that was always intended; it
  does not widen it.
