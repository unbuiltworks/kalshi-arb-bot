# Pre-registration: the ADVERSE_EXTEND_REFUSAL candidate

**Written and committed BEFORE any result for this rule was computed.**
That is the whole point of the file: the rule, its single parameter, the
data split, the metrics and the decision criterion are all fixed here, so
that whatever comes back cannot be re-described afterwards as what I
expected.

Commit this file first. Then run the evaluation.

---

## 1. The question

> Does refusing an EXTEND when our existing position has moved adversely
> improve net expectancy, after accounting for missed recoveries, missed
> profitable pairs, and reduced trading opportunities?

## 2. Where the rule came from, stated honestly

From the mechanism visible in three zero-completion production windows
(1745z, 1800z, 1815z), not from a parameter search:

EXTEND averages down into the side the market is leaving. As our side
falls, the complement rises, so a fee-safe completion at `<= 0.96 - avg`
becomes unreachable, no pair completes, and the position is carried to
expiry as a pure directional bet. Within those windows the loss was
monotonic in extend count across the three policies, which is what
suggested the rule.

**Those windows motivated it, so they cannot also test it.** Section 5 is
the out-of-sample design.

## 3. The rule, exactly

    ADVERSE_EXTEND_REFUSAL  =  ARGMAX, plus one refusal.

On a tick where the base policy (ARGMAX) would choose `relation ==
"extend"`:

    let  avg  = our average cost on the surplus side
    let wire  = the current own-outcome price of that same side
    if wire < avg:  decide NO_ACTION, reason "adverse_extend_refused"
    else:           behave exactly as ARGMAX

Everything else is untouched: same model, same pair cap (0.96), same
residual cap, same one-order-per-scenario rule, same fill model, same
queue assumptions, same capital limits.

### 3.1 There is no tunable threshold, deliberately

The test is the **sign** of `wire - avg`, not a magnitude. A magnitude
threshold is exactly the knob that would get fitted to the evaluation, so
the rule does not have one. If a bare sign test does not help, the answer
is that it does not help -- not that it needs tuning.

`avg` is the average-cost basis, because that is what `complement_target`
already prices completion against; using anything else would make the
refusal and the completion disagree about the same position.

## 4. What is NOT being changed

- No new service, no new execution path, no new deployed policy.
- The nine shadow simulations keep running unchanged throughout.
- The candidate is evaluated OFFLINE only. It is not promoted on this
  evidence and live trading is not activated.

## 5. Data and the chronological split

Two sources, used for different things, because they are good for
different things:

**(a) Frozen sequential replay** -- `data/l5_master/btc15m_sequences.jsonl`,
via the existing read-only replay. Split **chronologically**: the earliest
60% of windows is DEV, the latest 40% is HOLDOUT. The split is by time and
is fixed here, before any result.

LIMITATION, STATED UP FRONT: this replay's absolute P&L is NOT credible.
It reconstructs a synthetic one-tick spread, which earlier produced
nets of 24-33% against L5's own realized 0.30%. It is therefore used for
**behavioural** quantities only -- extend counts, completion rates,
residual units, how often the rule fires -- and its money numbers are not
reported as expectancy.

**(b) Production shadow records** -- the recorded decisions and modelled
fills from the live nine-simulation pipeline, replayed through the same
queue-aware fill model and settled at the venue's own `settlementPx`.
These carry realistic queue assumptions and authoritative settlement, so
this is the only source whose economics are quoted.

LIMITATION, STATED UP FRONT: as of writing there are **six** settled
windows, three of which motivated the rule. Six windows cannot establish
expectancy. The production arm is therefore DESCRIPTIVE until the window
count grows, and the pre-registered decision criterion in section 7
requires far more than six.

## 6. Metrics, fixed now

Reported for the candidate and for each of ARGMAX, RESIDUAL_URGENCY and
PAIR_AVAILABLE, on **identical observations and identical windows**, in
the CONSERVATIVE scenario:

1. **Losses prevented** -- fills the rule refused that would have been
   adverse, and the modelled cost of those fills.
2. **Profitable recoveries missed** -- refused extends where the position
   would subsequently have completed a pair, or where the side recovered
   above our average before expiry. This is the cost side of the rule and
   is reported with the same prominence as the benefit side.
3. **Pair completion rate** -- completed pairs per window, and the share
   of windows with zero completions.
4. **Residual exposure and net** -- residual units at expiry, residual
   net, pair net, and total net per settled window.

Plus: how often the rule fired, and how many windows it changed at all. A
rule that changes almost nothing cannot be judged by its average.

## 7. The decision criterion, fixed now

The candidate is worth proposing for shadow deployment only if ALL of:

- conservative net per settled window improves against ARGMAX on the
  **HOLDOUT** split, not only on DEV;
- the improvement is not produced solely by the three windows that
  motivated the rule -- it must survive their removal;
- pair completion rate does not fall;
- "profitable recoveries missed" does not exceed "losses prevented".

And even then: proposed for SHADOW, with at least 30 settled production
windows before any live discussion. Six is not a basis for anything.

If the criterion is not met, the result is reported as a negative result
and the rule is dropped. A negative result here is a success for the
measurement, not a failure of the work.

## 8. Threshold-selection ban

No threshold, split boundary, window exclusion or metric defined in this
file may be changed after results are seen. If something here turns out to
be mis-specified, the fix is a NEW pre-registration with a NEW evaluation,
recorded as such -- not an edit to this one.
