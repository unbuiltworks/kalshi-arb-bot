# The three incentive lines, and the one that paid for depth has stopped

Verified against `analysis/l5/incentive_forensics/l5_incentives_daily.json`
(239 rows, **`rows_with_conditionId: 0`** — the venue publishes no per-market
incentive record for another account, so every figure here is account-level and
`attribution_verdict` is already `UNATTRIBUTABLE`).

## The split, and the window it belongs to

The $20,490.13 is **2026-09-01 .. 09-10**, matching the trading net and the 4,217
resolved markets. Searched over every contiguous 10-day span, exactly one matches:

| line | Sep 01–10 |
|---|---:|
| MAKER_REBATE | **$11,113.45** |
| REWARD (liquidity) | **$8,229.66** |
| TAKER_REBATE | **$1,147.02** |
| total | **$20,490.13** |

Confirmed exactly. **A caution against mixing windows:** full-September (15 days)
gives MAKER_REBATE $19,466.84 and TAKER_REBATE $1,499.17. The 4.06× ratio is only
valid against the Sep 01–10 trading net.

## THE REWARD LINE STOPS ON 2026-09-06

`REWARD` appears on **26 days in total, the last being 2026-09-06**. Within the
window it is Sep 1–6 only: 1174.30, 1502.63, 1605.57, 1158.91, 1272.53, 1515.71.

| regime | days | maker/day | reward/day | taker/day | total/day |
|---|---:|---:|---:|---:|---:|
| Sep 01–06, reward live | 6 | $1,043.62 | **$1,371.61** | $146.50 | **$2,561.73** |
| Sep 07–15, reward absent | 9 | **$1,467.23** | **$0.00** | $68.91 | **$1,536.14** |

* While live, REWARD was **53.5%** of incentives.
* Its loss cut the incentive run-rate **40.0%**.
* **The maker rebate ROSE 40.6%** ($1,043.62 → $1,467.23/day) once it stopped.

Whether the programme paused or the capture missed later days is **UNKNOWN** — the
file was fetched 2026-09-15 and `MAKER_REBATE`/`TAKER_REBATE` continue to that
date, so the absence is not a truncated capture, but a programme change cannot be
distinguished from an account-level change from these rows.

## This inverts the strategic reading

The owner's reading of the schedules — maker rebate paid for **fills** as a share
of the taker-fee pool with no uptime, depth or two-sided term; REWARD paid for the
**book** via `((v−s)/v)²` with two-sided boosted and single-sided divided by three
inside 10–90¢ — is **not documented anywhere in this repository** and is recorded
here as owner-provided, not repo-verified.

Taking it as given, the data says something sharper than "the ladder is the cost of
the reward score":

> **The line that paid for resting depth is gone, and L5 remained profitable
> without it — on the line that pays for fills, which grew.**

Even assuming the trading loss rate held (−$504.79/day over Sep 01–10, a window
spanning *both* regimes, so this is an assumption and not a measurement),
maker+taker alone at $1,536.14/day leaves roughly **+$1,031/day**.

So the durable business is **maker-rebate harvesting on executed volume**, not
depth posting. A clone built to reproduce a two-sided ladder would be reproducing
the behaviour whose payment stream has already ended.

## The gap on the durable line only

His maker-rebate rate on the durable regime: ~$1,467/day against ~$156,350/day of
spend (spend is only available for Sep 01–10, so this mixes windows and is
approximate) ≈ **0.94% of spend**. Our modeled PMUS maker credit on V1 q10 is
**0.335% of notional** — a different formula (`0.0125 × C × p × (1−p)`, at-trade),
and **modeled, not credited**. Our credited evidence remains **$0.42 across 26
markets**.

So on the durable line the gap is roughly **2.8× in rate × 20.4× in notional ≈
57×**, rather than the ~80× computed against the blended figure.

## Withdrawals I accept, restated

* **The mint-feasibility split is withdrawn** — `best_ask(X) + best_bid(complement)
  = 1.0000` in all 8 cases, so a buy at the ask is mint-feasible by construction.
  It restates position. The same arithmetic removes the force of the Zenodo 99.86%;
  the informative remainder there is the **1.49%** at or below the bid.
* **The three-way pre-resting verdict is unusable** on this population — a buyer at
  the ask is not evidence about bid depth.
* **8 of 12 at/above ask is observed, not a mechanism.** A resting bid cannot print
  at the ask; a mint can and a taker buy can; the activity row has no maker/taker
  field. On n = 12 it is a discrepancy against the August 26.4% and the historical
  82.7% maker→maker share, not a replacement for either.
* **Nothing is built from the twelve fills**, and the missing chain role is not a
  reason to keep searching the fill tape.

What remains usable from the tape: the `transaction_hash` join and the **0.9993**
delta-semantics validation.

## Classification

**OBSERVED** — the three-line split and its exact window; REWARD's 26 days and
2026-09-06 last appearance; the two regimes' run-rates; the 40.6% rise in maker
rebate; `rows_with_conditionId: 0`.

**INFERRED** — that the durable business is fill-driven rather than depth-driven;
the ~+$1,031/day residual, which assumes a trading loss rate measured across both
regimes.

**UNKNOWN** — whether REWARD paused programme-wide or for this account; what each
line was contractually paid for (the formulas are owner-provided, absent from this
repo); and whether PMUS offers anything comparable to either pool.
