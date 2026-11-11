# Residual repair: pre-registration

**Frozen before any outcome was examined.** Written and committed before the
reconstructor was run, because the deliverable the owner asked for is an
**OOS-valid residual rule**, and a rule chosen after seeing which choices
would have paid is not OOS-valid however it is later presented.

Owner, 2026-09-11: *"reconstruct them without settlement hindsight, beginning
at the first point each market became imbalanced. For each subsequent decision
state, evaluate what was actually knowable and executable then."* And: *"We
don't merely want a repair algorithm that makes our backtest look better; we
want to know whether L5 already demonstrates the repair behavior we're
missing."*

---

## 1. The sample, stated before it is used

17 R5F markets settled carrying a directional residual. Sum **−$30.5921**,
mean −$1.9120, 95% CI [−2.9588, −0.6657] — entirely below zero, which is why
residuals are the dominant failure mode. **Two of the 17 are positive**
(+$5.1884 and +$1.4610); their residuals resolved favourably.

**The honest prior, recorded now:** seventeen markets is a very small sample
for fitting any decision rule, and two favourable outliers dominate the right
tail. A rule with more than a couple of free parameters will fit noise here
and cannot be rescued by cross-validation on the same 17. I expect the most
likely honest outcome of this work to be *"the evidence supports a direction
but does not validate a rule"*, and that outcome will be reported as such
rather than converted into a promotion.

`CLAUDE.md` standing constraint, restated because it binds this analysis
directly: **do not assume residual directional exposure has predictive
skill.** The +$5.1884 market is not evidence of skill unless a rule that could
not see its outcome would have chosen to hold it. Any rule whose advantage
disappears when those two markets are removed is reporting luck.

## 2. Unit of analysis

One record per **post-imbalance decision state**: every decision the engine
logged for a residual market from the first state where
`inventory_before.residual != 0` through the market's last session.

## 3. What counts as knowable at a state

Only fields carrying information available at that timestamp:

| knowable | source |
|---|---|
| inventory before the action | `decisions[].inventory_before` |
| L5 state key (price / TTE / imbalance bucket) | `decisions[].state` |
| the touch on the side we acted | order `px` (OPEN/EXTEND price **is** the BBO touch by construction) |
| opposite-side ask | `complement_preflight.opp_ask` |
| L5's own touch for a complete | `counterfactual_l5_price.l5_touch_px` |
| whether a complete would cross | `counterfactual_l5_price.would_cross_at_touch` |
| pair cost implied at that touch | `counterfactual_l5_price.pair_at_touch` |
| time to settlement | `state` TTE bucket, `t_utc` |
| L5's action distribution at this state | `persist_extend_p`, `switch_complete_p`, `state_n` |
| L5's size at this state/relation | order `l5_target_size` percentiles |

**Explicitly forbidden as an input to any rule:** the settlement outcome, the
resolution side, `realized_usd`, the final inventory, and anything derived
from them. The reconstructor must not read them to *choose*; it reads them
only to *score*, and the train/holdout split below governs that.

## 4. Action classes at each state

1. `COMPLETE` — buy the deficit side, reducing `abs(residual)`.
2. `EXTEND_REPARATIVE` — buy the surplus side **at a price that lowers the
   matched pair cost**, i.e. an extend whose fill would reduce
   `avg(surplus) `. This class exists because of the measured
   `aec-atp-nikizq-santav-2026-09-08` case, where NO extends near 0.38 pulled
   `avg_dn` from 0.49 to 0.4226 and dug the pair out from 1.15 to 1.0732.
   Repair, not added risk.
3. `WAIT_REQUOTE` — no order, or re-post at a better price, and re-evaluate.
4. `NO_SAFE_ACTION` — every option breaches a standing bound (pair ceiling,
   residual cap, spend cap, band, post-only).

Classification is by the **then-current** bounds, not today's.

## 5. The metric

For each state, the **attainable completion cost**: the cash needed to flatten
`abs(residual)` at the then-current opposite touch, versus the guaranteed
settlement floor if we do not. Both are computable without hindsight.

A repair opportunity is **missed** when, at some state, a COMPLETE or
EXTEND_REPARATIVE was available inside every standing bound and the engine
instead did nothing, and the market later settled worse than that action's
floor. Missedness is judged on the *floor*, not on the resolution side.

## 6. OOS design, fixed now

- **Split:** markets, not decisions, and chronological. Train = the 9
  earliest-settling residual markets; holdout = the 8 latest. Chronological
  because a random split leaks regime.
- Any rule is specified on TRAIN only. The holdout is read **once**.
- **Sensitivity, mandatory:** every headline number is reported again with
  the two positive-residual markets excluded. If the rule's advantage depends
  on them, that is the finding.
- **Parameter budget: at most two free parameters.** More than two on 9
  training markets is fitting noise.

## 7. The promotion bar, set before the result is known

A residual rule may be proposed for shadow evaluation only if **all** hold:

1. On the holdout, it improves summed floor-at-settlement versus what the
   engine actually did.
2. The improvement survives removing the two positive-residual markets.
3. It never proposes an action that breaches a standing bound — pair ceiling
   ≤1.00, residual cap, spend cap, maker-only, one outstanding order.
4. Its action at each state is **more L5-like, not less**, measured against
   `persist_extend_p` / `switch_complete_p` at that state. A rule that earns
   by diverging from L5 is a different strategy and must be labelled one.
5. It is stated in ≤2 parameters.

Clearing this bar earns **shadow evaluation only**. It does not earn a live
change. Per the owner: R5F live configuration is unchanged while this runs.

## 8. What would falsify the repair hypothesis

- Missed repair opportunities are rare (say <1 per residual market), which
  would mean residual losses are set at entry, not by post-imbalance
  management, and the lever is selection rather than repair.
- Available completes at every post-imbalance state were already above the
  pair ceiling, meaning the loss was locked before the imbalance was visible.
- L5's own distribution at these states favours neither COMPLETE nor
  reparative EXTEND, which would mean we are proposing behaviour L5 does not
  exhibit and the anti-drift rule applies.

## 9. The L5 comparison, and its known weakness

At each post-imbalance state, compare the engine's chosen relation against
L5's empirical distribution at the same state key (`persist_extend_p`,
`switch_complete_p`, `state_n`).

**Weakness stated up front:** the state key buckets price, TTE and imbalance
coarsely, so "analogous inventory state" is analogous only to bucket
resolution, and `state_n` is small for some buckets. Any comparison drawn
from a bucket with `state_n < 20` will be reported with that n attached and
not aggregated with better-supported buckets.

---

**Committed before running.** Deviations from this document must be recorded
as deviations, with the reason, not silently applied.

---

# Amendment 1 — the repairing-EXTEND rule (2026-09-11)

**Recorded before the rule was written or run. The holdout remains unread.**

Owner: *"Build a decision-time metric for a 'repairing EXTEND'. A candidate
EXTEND should only qualify if it improves the future inventory geometry
without worsening the bounded downside."*

## The target, computed before the rule exists

From the corrected R5F cohort (full evidence):

```
paired to zero   36 markets   mean  +0.2851
left residual    16 markets   mean  -1.9120
residual incidence            16/52 = 30.8%
one residual erases 6.7 paired markets

break-even residual incidence, trading only        12.98%
  + rebates at  7 credited fills/market ($0.094)   17.25%
  + rebates at 10 credited fills/market ($0.134)   19.07%
```

**The rule must plausibly move residual incidence from ~31% to under ~17%.**
A rule that improves per-market floor but leaves incidence near 31% has not
solved the problem. Throughput is explicitly NOT a lever here: it scales
both sides of the ratio and leaves break-even untouched.

## Inputs — all decision-time, all already in §3

The owner named seven: resulting residual size; new effective average on
that side; achievable complement price; remaining time; fill probability;
resulting worst-case loss; expected rebate on the maker fill.

Each is computable at the state without hindsight:

| component | decision-time source |
|---|---|
| resulting residual | `inventory_before` + candidate qty |
| new average on that side | `avg_up`/`avg_dn` + candidate px, qty |
| achievable complement price | `complement_preflight.opp_ask`, `l5_touch_px` |
| remaining time | `state` TTE bucket, `t_utc` |
| fill probability | measured base rate by relation (extend 35.5%, complete 10.5%) and distance from touch |
| resulting worst-case loss | `min(up,dn) − spent` after the candidate fill |
| expected rebate | `0.0125·qty·p·(1−p)`, banker's-rounded — **validated 7/7 against the venue 2026-09-11** |

## The parameter budget is NOT waived

§6 fixes **at most two free parameters**, and seven inputs do not license
seven knobs. The inputs enter as *derived quantities*; only the thresholds
are free. The rule form is fixed here, before fitting:

> An EXTEND qualifies as REPAIRING iff
> 1. it strictly lowers that side's average cost (a pure inequality, no
>    parameter), **and**
> 2. the resulting guaranteed floor worsens by no more than **θ₁**, **and**
> 3. it is priced within **θ₂** ticks of the touch (the fill-probability
>    proxy; joining the touch is what the measured 35.5% extend fill rate
>    was earned at).

Two parameters, θ₁ and θ₂, fitted on TRAIN only.

Expected rebate is carried in the **reporting**, not the qualification: at
$0.0134/fill it cannot justify an otherwise-bad extend, and letting it into
the gate would be the "rebate makes a bad trade look profitable" failure
`CLAUDE.md` forbids.

## Additional bar, on top of §7

7. **Residual incidence, not just per-market floor.** Report the counterfactual
   incidence the rule implies. If it does not credibly move 31% toward 17%,
   report that it does not, rather than presenting a floor improvement as a
   solution to the incidence problem.

## What has NOT changed

The §6 split (9 train / 8 holdout, chronological, holdout read once), the
mandatory sensitivity with the two positive-residual markets removed, the
§7 bar, and the rule that clearing it earns **shadow evaluation only** and
never a live change. R5F configuration is frozen per the owner.
