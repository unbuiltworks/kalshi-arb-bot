# Pre-registered policy for the next bounded live probe — `RESIDUAL_CAP_k2`

Registered from `analysis/l5/RUN_LENGTH_FINDINGS.md` (`d559c4c`). **Not
implemented and not armed.** The executor stays frozen at `e2bd442`, DISARMED.

## The rule, exactly

On top of frozen `L5_NATIVE`, in the `SAFETY_OVERLAY` layer, one addition:

```
cap  = 2 * max(largest clip already filled in this market, this candidate's own qty)
after = residual_after_this_fill
refuse iff  this acquisition INCREASES |residual|  and  |after| > cap
```

Three properties that define it as much as the formula does:

- **It never refuses a repairing leg.** A leg that reduces \|residual\| is
  always allowed, at any size, at any time. `COMPLETE` is untouched.
- **It never refuses an opening leg.** The candidate's own size counts toward
  the clip, so an empty market cannot be blocked by a cap derived from no
  history.
- **It never invents a trade.** It is a refusal, never an instruction to buy,
  sell, hedge, or flatten.

Reference implementation exists and is tested:
`src.l5_expectancy_replay.ResidualCap(k=2)`.

Everything else in the current envelope is unchanged: `max_usd_per_market 5`,
`max_abs_residual 10`, `max_total_exposure_usd 5`, `max_concurrent_markets 1`,
`max_live_orders 8`, `daily_realized_loss_halt_usd 5`, `PMUS_RPS 1.5`, maker
first, one market at a time, settlement-only realized P&L labelling. No
pair-cost ceiling — §3 of the findings shows a 0.96 pair ceiling is
catastrophic in this regime because it refuses *completing* legs.

At our clip sizes (a $5 market at prices near $0.50 gives clips of roughly 2–5
contracts) the cap binds at about 4–10 contracts, i.e. at or inside the
existing `max_abs_residual 10`. It is a tightening, never a loosening.

## Predicted economic mechanism

L5's residual is symmetric directional exposure with a persistent small
negative edge (`E[won] − price` sits near −0.01 at every run length, which is
ordinary adverse selection on a buyer). It is not a loss to eliminate and it
has no demonstrated predictive skill in either direction. The cap therefore
does **not** claim to trade residual better. It claims one thing:

> Bounding \|residual\| in units of the session's own clip removes the tail of
> the exposure distribution while leaving the matched-pair and maker pipeline
> that generates the economics intact.

Historically that is 83.7% of L5's actions and 78.5% of his contracts
retained, against peak \|residual\| down 67% and max drawdown down 68%.

## What the probe tests, and what it cannot

Falsifiable, and measurable inside a $5 envelope:

1. `|residual| <= 2 * max_clip_seen` holds at **every** point in every market.
   A single violation is a defect, not a result.
2. When the cap binds, the next complement is still taken — repair is never
   refused.
3. The cap binds at least once across the probe. If it never binds, the probe
   is uninformative rather than successful, and says so.
4. **Superseded — see the correction below.** ~~Matched contracts per market
   are not materially below the frozen `L5_NATIVE` probe's observed rate.~~

**Not** testable here, and not claimed: that P&L improves. Every out-of-sample
interval in the findings spans zero across 2,216 windows; five markets at $5
resolve nothing about profit. Any P&L from this probe is a sample, not
evidence for or against the rule.

## Standing constraints that this does not touch

The $5 worst-case risk gate is unchanged, and gross cash deployed is still
reported separately from it. Maker rebates stay on their own line and become
income only when the venue credits them — the findings show L5's own tape
rated at the PMUS fee schedule is **−$1,020 net pre-rebate**, so no probe here
may be scored as profitable on accrued-but-uncredited rebates.

Arming still requires `PMUS_Q1_LIVE=YES`, a matching `PMUS_Q1_ARM`, an
authoritative preflight PASS, and explicit authorization.


---

## Correction, 2026-09-27, before any live result

**Criterion 4 as first written was wrong and is replaced.** "Matched contracts
per market are not materially below the frozen `L5_NATIVE` rate" is
inconsistent with the policy it was meant to test: a cap that binds removes
acquisitions, and some of those acquisitions would later have matched. It
cannot mean zero matched-volume loss, and read that way it would fail by
construction on the first bind. Read the other way — "not materially below" —
it is undefined, which is worse: an undefined bar is one that gets defined
after the result is in.

Nothing live has run under this policy. The executor has never been armed with
it. This is a logical defect fixed before any data exists, not a bar moved
after seeing one.

### Replacement: a predeclared retention floor from the OOS replay

The replay's retention numbers for `RESIDUAL_CAP_k2`, which are what the
floors are set from:

| | DEV | OOS | whole corpus |
|---|---|---|---|
| actions retained | 83.4% | 84.1% | 83.7% |
| contracts retained | — | — | 78.5% |
| matched contracts retained | 83.9% | 83.7% | — |
| maker fills retained | 83.8% | 83.9% | — |
| matched **P&L** retained | 78.6% | **45.6%** | — |

Matched *volume* is stable across halves; matched *P&L* is not (78.6% → 45.6%),
which is a direct consequence of the cap removing the tail of the exposure
distribution in both directions. So the floor is set on volume, where the
estimate is stable, and no floor is set on matched P&L, where it is not.

**Predeclared, now:**

- **action retention ≥ 0.75** — submitted ÷ (submitted + RESIDUAL_ABOVE_CAP),
  counted over the probe.
- **contract retention ≥ 0.70** — the same ratio weighted by order quantity.

Both floors sit below both halves' point estimates. Both are measured
**inside the probe as a paired ratio**, not against a historical rate: because
the cap is evaluated last, behind every frozen check, a `RESIDUAL_ABOVE_CAP`
refusal is by construction an action the frozen executor *would have
submitted*. The denominator is therefore the counterfactual NATIVE action
count on the very same market, and no cross-sample comparison is involved.

### What these floors can and cannot decide

At the authorized envelope — one market, $5, a handful of orders — the probe
will produce on the order of tens of decisions. That is enough to compute the
ratio exactly, and **not** enough to distinguish 0.84 from 0.70. Breaching a
floor is therefore informative (the cap is binding far harder live than in
replay, and the probe stops); clearing one is not evidence that retention
matches the replay.

The only criterion this probe can actually decide is the invariant in item 1,
which is a property of every single decision rather than an average over them.
