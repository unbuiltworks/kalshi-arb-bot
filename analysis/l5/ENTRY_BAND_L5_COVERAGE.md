# Entry band vs L5's own pre-fill coverage

Why the OPEN admission band moved from **0.35-0.65** to **0.25-0.75** on 2026-09-10, on the owner's GO ("We need more trades").

## Provenance

Measured against `analysis/l5/l5_action_model_v2.json`, sha256[:16] = `bbb55bf90638d2bd` — byte-identical to the `model_sha` stamped on every live decision row, so this is the model the probe actually runs, not a copy. Nothing in the frozen model was modified; it was read.

Coverage = L5's own **pre-fill OPEN** observations, i.e. `base` states with `|flat` inventory carrying an `open|*` action. That is exactly the lookup the OPEN admission path uses (`pre_fill:first_action_is_open`).

## Pre-fill OPEN observations, price band x TTE

`.` = L5 has no policy in that cell.

| band | tte<30s | tte30-120s | tte2-5m | tte5-10m | tte>10m | total |
|---|---|---|---|---|---|---|
| `p<10c` | 1 | 14 | 10 | 4 | . | **29** |
| `p10-25c` | 3 | 17 | 30 | 33 | 9 | **92** |
| `p25-45c` | . | 14 | 26 | 31 | 83 | **154** |
| `p45-55c` | 3 | 13 | 36 | 20 | 47 | **119** |
| `p55-75c` | . | 23 | 34 | 21 | 65 | **143** |
| `p75-90c` | 2 | 19 | 24 | 13 | 13 | **71** |
| `p>90c` | . | 14 | 10 | 8 | 3 | **35** |

Total pre-fill OPEN observations: **643**.

## What each candidate band admits

A price band counts only when the entry window contains it **whole** — a partially-admitted band still refuses most of its states.

| entry band | full bands | L5 pre-fill opens reachable | bands |
|---|---|---|---|
| 0.35-0.65 *(previous)* | 1 | 119/643 (18.5%) | `p45-55c` |
| 0.25-0.75 **(chosen)** | 3 | 416/643 (64.7%) | `p25-45c`, `p45-55c`, `p55-75c` |
| 0.10-0.90 | 5 | 579/643 (90.0%) | `p10-25c`, `p25-45c`, `p45-55c`, `p55-75c`, `p75-90c` |
| 0.00-1.00 | 7 | 643/643 (100.0%) | `p<10c`, `p10-25c`, `p25-45c`, `p45-55c`, `p55-75c`, `p75-90c`, `p>90c` |

## The finding

**0.35-0.65 admitted exactly one price band whole** — `p45-55c`, 119 observations,
18.5% of L5's pre-fill OPEN evidence. We had restricted ourselves to the
*third*-richest band and excluded the two richest: `p25-45c` (154) and
`p55-75c` (143). Both bands we were refusing are better supported in L5's own
history than the one band we were trading.

0.25-0.75 admits all three whole: **416/643 = 64.7%**, a 3.5x increase in
reachable L5 policy.

## Why not wider

0.10-0.90 reaches 90%, but adds `p<10c` (29 observations) and `p>90c` (35) —
thin policy, and the price extremes are where pair economics and residual
directional risk diverge most from the middle. The two bands added by 0.25-0.75
are each better supported than the band already in use; that is not true past
0.75 or below 0.25.

## What did NOT change

- **No money limit moved.** Per-action $2.50, per-leg $3.25, per-market $7.50,
  residual 6 units, daily loss stop -$15.00 — all untouched.
- **The completion ceiling did not move.** `pair_cost_max` stays <= 1.00; the
  1.00-1.03 rescue band remains unbuilt and unauthorised.
- **The outer envelope did not move.** `pmus_q1_executor.MIN_PRICE/MAX_PRICE`
  remain 0.20-0.80. 0.25-0.75 sits strictly inside it, so this change cannot
  admit anything the micro layer would not already have permitted.
- **The distribution did not widen.** Cells with no L5 policy — `p25-45c` and
  `p55-75c` below 30s to expiry — still fail closed on `off_distribution_hold`.
  Widening *admission* never widens the *distribution*.

## Limitation, stated plainly

This does not predict how many more trades we will get. The shadow refusal
counters overlap (3.04M `entry_band` + 5.49M `thin_flow` + 3.56M
`off_distribution_hold` against 5.7M decisions — each decision trips several),
so the marginal admission gain from moving only this band is not derivable from
them. What is measured here is the share of L5's own policy the band makes
reachable, which is a statement about fidelity, not about volume. The volume
answer comes from the live funnel after the change.
