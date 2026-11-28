# Can defensive completion make autonomous ARGMAX positive? — 2026-10-29

Production **disarmed** (`PMUS_Q1_LIVE=NO`). No new service, no new
strategy, no deployment. The only code added is a shadow recorder that
calls the **deployed** decision function in simulate mode.

## The answer: arithmetically yes, and the bar is lower than expected

ARGMAX as measured over 38 settled windows: `pair_net` **+10.1837**,
`residual_net` **−13.3237**, **NET −3.1400**. 34 of 38 windows carry
residual, 82 gross residual units, worst single window **−1.4700** (1830z).

The residual term does **not** need eliminating. It needs to improve from
−13.3237 to **−9.3757** — a **+3.9480** improvement — for ARGMAX to break
even, after charging a conservative pre-emption cost of −0.8080 (the
−0.1701 observed over 8 replay episodes, scaled to 38 windows).

### Sensitivity to the defensive completion rate `f`

`residual_term(f) = f × gain_if_all + (1 − f) × residual_net`, with
`gain_if_all = +1.3968` (every residual unit completed at the one observed
reachable defensive pair, 0.9656, with the real taker fee charged).

| f | residual term | **TOTAL** |
|---:|---:|---:|
| 0% | −13.3237 | −3.9480 |
| 14.3% | −11.2187 | −1.8430 |
| **26.8%** | −9.3786 | **−0.0029** |
| 30% | −8.9076 | +0.4682 |
| 42.9% | −7.0086 | +2.3671 |
| 50% | −5.9634 | +3.4122 |
| 100% | +1.3968 | +10.7725 |

**Break-even completion rate: 26.8%** at pair 0.9656, or **33.1%** at pair
1.0000 (pure loss-avoidance, zero gain per unit).

### The ceiling must be at or below 1.00 — the 1.20 default is destructive

| assumed completion pair | residual term | TOTAL |
|---|---:|---:|
| 0.9656 (observed reachable) | +1.3968 | **+10.7725** |
| 1.0000 (venue break-even) | −1.4104 | **+7.9653** |
| **1.2000 (`DEFENSIVE_PAIR_CEILING` default)** | −17.4688 | **−8.0931** |

At the 1.20 code default the policy is **worse than doing nothing**. A
matched pair redeems exactly $1.00 and the venue has no maker-fee term, so
any completion above $1.00 locks a guaranteed loss. The recorder therefore
passes `pair_ceiling=D("1.00")` explicitly and overrides the default.

## Why a causal replay over the 38 windows is impossible

Checked, not assumed:

- **1 of 38** ARGMAX windows has retained book frames
  (`cpc-btc-updown-15m-2026-10-29-2100z`).
- Settled records carry **no** `taker_walk`, `reachability`, `queue_ahead`,
  bids or asks — only end-state economics.
- The sampler emits at 5 s × 6 variants, so a 500-record log pull spans
  ~7 minutes. Covering 38 windows would need ~41,000 records.

So the executable depth, latency and queue state those windows had were
never retained, and **honouring "do not assume a historical quote survives
submission latency" means the rate cannot be recovered retrospectively.**
The 290-frame 20261025 dataset remains the only causal replay surface, and
it contains exactly **one** genuinely stranded episode.

## What the retained evidence says about the rate

From the 290-frame replay, 7 episodes carried a residual:

| | |
|---|---|
| defensive decision taken | 5 |
| decision FILLED at realistic latency | 3 (42.9% of 7) |
| — 0030z | pre-empted a better maker fill, **−0.1608**, not a rescue |
| — 0100z-B | pre-empted a better maker fill, **−0.0093**, not a rescue |
| — 0130z | **genuine strand rescue, +1.1937** |
| genuinely stranded episodes in the dataset | **1** |
| of those, defensively completable | **1**, at pair 0.9656, filled at both λ=0.5 and λ=8.818 |

**Required 26.8%; observed n=1 at 100%.** Encouraging, not established.

## Implemented: the shadow records the decision, forward

`Runner.shadow_defensive_decision`, called from `shadow_residual_book` after
each residual sample. It calls **`DEF.decide(simulate=True)`** — the
deployed policy, not a second copy — with the first-leg cost now available
from the previous instrumentation fix, the sampler's executable depth and
taker walk, the real `pmus_fees` taker fee, the maker complement price the
engine would actually quote, and `pair_ceiling=1.00`.

`DEF.alternatives` inside it prices the courses side by side in dollars.
Verified output at first leg 0.45 on a 0.52/0.53 book:

| course | best | worst | executable now |
|---|---:|---:|---|
| `HOLD_TO_SETTLEMENT` | +0.55 | −0.45 | yes |
| `MAKER_COMPLETE_AT_CEILING` | +0.1000 | +0.1000 | **no** |
| `DEFENSIVE_UNWIND` | **+0.0527** | **+0.0527** | yes |

→ `DEFENSIVE_EXIT_BOUNDED_AND_FUNDED`. At first leg 0.62 (pair 1.10) and
0.80 (pair 1.28) it is correctly refused `DEFENSIVE_PAIR_CEILING`.

### The decision rule the numbers imply

For a residual at own cost `p`, holding to settlement is a coin flip worth
`0.5 − p`; crossing at pair `c` is a certain `1 − c`. So:

```
cross beats hold   iff   1 - c > 0.5 - p   iff   c < 0.5 + p
```

At p=0.45 the unwind is +0.0527 certain against a hold EV of +0.0500 **and
removes the ±0.50 swing** — better in expectation and free of variance.
That variance is not free to us: it is what the residual cap, the
worst-case deficit gate and the daily loss halt each price. A single
constant ceiling cannot express `0.5 + p`, which is why 1.00 is used as a
**cap** (above it the loss is certain) rather than as the decision rule.

It cannot trade: no transport, no `claim_submission`, no store write, and
`maker_only_forbids_defensive_crossing` is untouched.

**Tests:** 14 new in `tests/test_l5_shadow_defensive_decision.py`
(27 with the instrumentation file), all pass. Full suite FAILED+ERROR set is
**77, identical to the pristine baseline** — no regressions.

## GO / NO-GO

### NO-GO for autonomous bounded live validation — the rate is unmeasured

The economics are credible and the bar is low (26.8%), but `f` is observed
at n=1. Authorising capital on a one-episode rate would be betting on the
only observation we have, and the same replay shows the policy *also*
pre-empts profitable maker fills, which is a recurring cost.

### GO for one deployment of the existing disarmed service

Economic justification, as required: this single deploy activates both the
instrumentation fix and the recorder, which together produce **the one
number that decides a money-path build** — the defensive completion rate at
a pair ≤1.00 on PMUS. It is read-only, the executor stays disarmed, and it
adds no service and no recurring cost.

### The specific remaining economic blocker

> **The defensive completion rate on PMUS at a pair ≤ 1.00 is unmeasured
> (n=1), and the threshold is 26.8%.** Everything else is settled: pair
> economics are good (median 0.7625, 100% < $1.00), the deficit is
> residual-only (−13.3237 against +10.1837), the opening side cannot fix it
> (every price band is net-negative), and the completion ceiling must be
> ≤1.00 or the policy is worse than nothing.

### Evidence that would convert this to GO

From the recorder, over **≥30 windows**:

1. `shadow_defensive:DEFENSIVE_EXIT_BOUNDED_AND_FUNDED` on **≥27%** of
   residual samples that reach the arm window;
2. the achieved `pair_if_taker` on those **below 1.00**, median at or under
   the 0.9656 already observed;
3. `DEFENSIVE_UNWIND.best_usd` exceeding the `HOLD_TO_SETTLEMENT` midpoint
   on the same samples, so the cross is justified on EV and not only on
   variance;
4. no window where the recorder would have crossed above `0.5 + p`.

Then the bounded live validation is the one already specified: 4 windows,
one market, `DEFENSIVE_COMPLETION_ONLY` scope under a fresh approval,
ceiling 1.00, `DEFENSIVE_LOSS_BUDGET_USD` 2.00, halt after the first
crossing, every other limit unchanged and upstream.
