# PREREGISTRATION — CELL_C_HOLD_NO_REENTRY_V1

Frozen before any result for this controller was computed, on either the
development tape or the out-of-sample tape. Committed first, deliberately, so
the rule cannot be adjusted after seeing a number.

## Hypothesis under test

> ONE ENTRY -> selective EXTEND -> HOLD/WAIT -> SETTLEMENT

The claim is narrow and economic, not behavioral: that frozen Cell C's
negative net is produced by **repeatedly paying taker fees and crossed
spreads to realize reversals**, and that deleting that sleeve — without
touching entry or extension — converts the same architecture into a
positive-expectancy hold/extend process.

This is a **counterfactual replay experiment**. It is explicitly NOT a claim
that the 21 naturally held-to-settlement episodes in the frozen log prove
profitability: 21 episodes that happened to survive to settlement are a
conditioned, self-selected subset, and their +$1.25 net is not evidence about
a controller that holds everything.

## Grok's frozen-log decomposition (the motivation, not the result)

| sleeve | episodes | gross | fees | net |
|---|---|---|---|---|
| naturally held to settlement | 21 | +$1.33 | $0.09 | **+$1.25** |
| trail-flatten / re-entry | 279 | +$8.70 | $11.33 | **-$2.63** |

EXIT is ~48% of base fees; EXTEND is ~4%. That asymmetry is what makes the
counterfactual worth running. It is not the counterfactual's answer.

## The controller, exactly

`CELL_C_HOLD_NO_REENTRY_V1` is frozen Cell C with the SELL sleeve deleted and
nothing else changed:

- **Initial entry** — timing, side, size preserved verbatim.
- **EXTEND decisions** — preserved verbatim, NOT optimized. Cell C extends by
  buying $1 of the current favorite at every 60 s mark
  (`require_strengthening=False`). That rule reads only the book's own mid; it
  does not read episode state, peak, trail, or inventory. Therefore deleting
  the exits **cannot** perturb the purchase stream, and the purchase legs are
  byte-identical between the two controllers. This is asserted structurally at
  runtime, not assumed (see Verification).
- **Routine trail SELL exits** — disabled (`exits_enabled=False`). The frozen
  trail `spread_at_peak + 2 x taker_fee(peak, 1)` is still *computed*; it
  simply no longer fires an order.
- **Re-entry churn** — prohibited as a consequence rather than as a second
  rule. With no flatten there is no post-flatten rebuild: every purchase after
  the first is a continuation EXTEND of one accumulating position, never a
  re-entry.
- **WAIT replaces every removed action.** WAIT costs zero and is a first-class
  action.
- **Remaining inventory is held to settlement.** There is no end-of-window
  liquidation; terminal inventory is valued by
  `settlement_value(outcome)`.
- **No settlement information enters any decision.** The outcome is consumed
  only after the last book has been walked.
- **Same executable prices, same depth discipline, same fee treatment.** Taker
  UP pays `ask` against `ask_qty`; taker DOWN pays `1 - bid` against
  `bid_qty`. No touch=fill. Queue-ahead unchanged.

## Explicitly NOT in V1

- No price threshold of any kind.
- No entry-price optimization.
- No maker assumptions — entries and the (now absent) exits stay taker.
- No window selection. Every window passing the frozen eligibility gate
  (span >= 600 s, last book within 30 s of scheduled end, terminal touch
  0.99/0.01) is reported. No window is dropped after seeing its outcome.
- No tuning on the original 28 development windows, and — whatever the OOS
  result — **no tuning on OOS**. A negative OOS result is an answer, not a
  starting point for a search.

## Verification (asserted, not asserted-to-be-true)

1. **Purchase-leg identity.** Every purchase leg (t, side, px, shares, fee) of
   HOLD_NO_REENTRY must equal frozen Cell C's, in order. A single mismatch
   fails the run, because it would mean the counterfactual silently changed
   entry or extension.
2. **Zero sell legs.** HOLD_NO_REENTRY must report 0 exits and 0 realized
   sale cash.
3. **Five-term identity.** directional + matched + settlement residual +
   verified compensation - fees = net, reconciling to 0.000000, for both
   controllers in every window.

## Fee treatment — both columns, every table

Reported side by side, after the independent audit:

- **modeled**: four-decimal theoretical fee (what the frozen replay charged).
- **venue-cent**: the published schedule's own banker's rounding to the cent,
  `taker_fee(px, sh, cents=True)`, recomputed per leg.

The modeled column overstates fees by roughly 2% (~$0.11 per window at ~$100).
Both are reported; neither is presented alone.

**Unresolved and flagged:** the taker coefficient's effective date. The code
uses 0.0695 from 2026-09-17 04:00 UTC (CFTC notice); current PMUS docs
apparently say Oct 1. Windows on Sep 30 - Oct 1 are affected. This is NOT
resolved by this experiment and is called out wherever it bites.

## Reporting

DEVELOPMENT (the frozen 28) and OOS are reported **separately**, never pooled.
For each controller on each sample: eligible windows, traded windows,
actions/window, ENTRY/EXTEND/EXIT counts, purchase cash, sale cash, settlement
cash, gross before fees, modeled fees, venue-cent fees, net, gross and net per
traded window, median, positive %, LB1/LB3 (leave-worst-1 / leave-worst-3),
worst window, peak capital, peak residual, and deployed/peak-capital turnover
— labeled as **capital turnover**, not as the mislabeled "turnover=30.24"
figure, which was remaining acquisition cost basis per window.

Distribution, not only the mean: the count of traded windows with gross >= $5
and with net >= $5 at each defensible capital envelope.

Inference is by **window-level** bootstrap. Settlement is one binary draw per
window, so the development sample has ~28 effective observations and any
per-window mean carries a wide interval. That is stated, not buried.

## Decision rule, fixed in advance

- **If OOS net per traded window is positive** and the mechanism is the
  predicted one (gross preserved, fee sleeve removed), quantify whether
  scaling inside the defensible ~$100 envelope can plausibly reach $5 net per
  traded window — and say plainly if it cannot.
- **If it is not**, STOP. That establishes that removing churn is
  insufficient and that the architecture needs a stronger entry/extension
  edge. No tuning follows.

No live strategy orders arise from this experiment.
