# Pre-registration: AT_TOUCH versus ONE_TICK_BEHIND

**Written and committed BEFORE the arm exists, before it runs, and before
any result.** The offset, the evaluation period, the decision criterion
and the stop conditions are all fixed here. Nothing in this file may be
changed after results are seen; a mis-specification requires a new
pre-registration, recorded as such.

---

## 1. The hypothesis, and the evidence that produced it

From `LOSS_DECOMPOSITION_20261019.md`, over eight settled windows:

- 63 of 69 conservative fills were at `ticks_behind_touch == 0`;
- `queue_ahead_at_placement` median **1,500** contracts;
- so fills come almost only when a print sweeps the whole level, which is
  when the market is decisively leaving;
- 70% of fills adverse, mean markout **−$0.0467** per unit;
- against **+$0.0050** of captured spread (half of one 0.01 tick), with
  fee $0.0000 CONFIRMED and rebate $0.0000 UNCREDITED;
- **9.3 : 1 against us.**

> **Hypothesis.** Quoting one tick behind the best bid widens the captured
> spread from half a tick to one and a half ticks and should reduce the
> share of fills that are sweeps, at the cost of a lower fill rate. The
> question is whether conservative NET improves — not whether the average
> price per fill improves, which it must by construction.

That last clause is the trap this comparison exists to avoid. A deeper
quote buys cheaper by definition. Only net matters.

## 2. The two arms

    AT_TOUCH          our own-outcome price at the best bid.  THE INCUMBENT:
                      this is the existing ARGMAX x 3 scenarios, unchanged.
    ONE_TICK_BEHIND   our own-outcome price exactly ONE TICK (0.01) worse.

"One tick worse in our own-outcome price" is the uniform definition across
sides: a YES quote bids one tick lower; a NO quote, whose own-outcome
price is `1 - ask`, quotes one tick lower in that same measure.

**The offset is exactly one tick and is not a free parameter.** It is not
tuned, not searched, and not re-chosen after results. If one tick does not
help, the answer is that one tick does not help.

**If one tick behind is not priceable** — at or below zero, or below the
venue minimum — the arm does NOT quote and the tick is recorded
`offset_not_priceable`. It is never silently rounded back to the touch.

## 3. What is NOT changed

- The live executor is untouched. The controller holds no transport and
  defines no order path.
- The three existing policies are untouched and keep running. This adds a
  PLACEMENT dimension to the BASELINE policy only: ARGMAX x 3 scenarios x
  ONE_TICK_BEHIND, three additional simulations, taking production from
  nine to twelve. Nothing is replaced.
- No new service, no new infrastructure, no change to any risk limit.
- Production stays shadow-only. No live orders.
- Same authenticated ladder, same queue-aware fill model, same settlement
  accounting, same pair cap, same residual cap, same one-order-per-arm
  rule.

## 4. Fill evidence — binding

The existing `l5_maker_fill_model` rules apply unchanged, and are restated
because they are the whole basis of the comparison:

- a **known** queue fills only when trade VOLUME clears `queue_ahead +
  our_qty` (CONSERVATIVE); BASE requires `>= queue_ahead`;
- an **unknown** queue never fills except on a sweep, and only
  OPTIMISTIC;
- a sweep through a known queue fills nothing;
- **a level shrinking is not queue advancement.** A cancellation ahead of
  us is indistinguishable from a fill ahead of us in the data we get, and
  crediting it would manufacture fills.
- **a price touch is never a fill.** Queue position is frozen at
  placement; only prints advance it.

A deeper quote has MORE queue ahead of it, so this matters more here than
anywhere: the arm must not be flattered by the one rule that would make a
deep quote look executable when it is not.

## 5. Evaluation period, fixed now

**The first 40 consecutive settled BTC15m windows whose window end falls
after the arm's deployment timestamp, or 12 calendar hours after that
timestamp, whichever comes first.**

Windows are counted whether or not either arm traded in them. No window
is excluded for being unfavourable, for being a no-fill window, or for any
other reason.

An interim read may be reported at 20 windows. **It carries no decision.**

## 6. Metrics, fixed now

Per arm, per scenario, on identical windows:

1. modelled fill frequency and volume at each quote level;
2. `queue_ahead_at_placement` distribution, and the known/unknown split;
3. spread captured per fill, and adverse-selection markout per fill;
4. matched-pair completions per window, and the zero-completion share;
5. residual units at expiry and residual net;
6. **conservative net per settled window, including no-fill windows**;
7. capital deployed, capital efficiency, max drawdown;
8. `offset_not_priceable` count, so an arm that mostly cannot quote is
   visible as that rather than as a low fill rate.

## 7. Decision criterion, fixed now

ONE_TICK_BEHIND is judged to improve conservative net expectancy only if
ALL of:

- **(a) Net.** Conservative net per settled window is strictly higher than
  AT_TOUCH over the full evaluation period, counting every window.
- **(b) Not by abstention.** ONE_TICK_BEHIND places at least **25%** as
  many quotes as AT_TOUCH. A quote that never executes cannot win by
  declining to trade; below this threshold the result is reported as
  **NOT EXECUTABLE AT THIS DEPTH**, which is a different finding from
  "better".
- **(c) Stability.** The net advantage holds on BOTH halves of the
  evaluation period (first 20 windows and last 20) in sign. Forty noisy
  windows do not support a significance test, so this is the stability
  check in its place, and it is stated as such rather than dressed up as
  one.
- **(d) Mechanism.** Adverse-selection markout per fill improves. If net
  improves while markouts do not, the hypothesised mechanism is not what
  produced it and the result is reported as unexplained rather than
  adopted.

If (a)–(d) hold, the outcome is a recommendation to run the arm longer and
a proposal for shadow promotion. **It is not a live-trading proposal and
does not authorise one.** Any live step needs separate authorization and
far more evidence.

If any of (a)–(d) fails, the result is reported as negative and the
hypothesis is dropped, as `ADVERSE_EXTEND_REFUSAL` was.

## 8. Stop conditions, fixed now

Stop early and report, before the 40 windows, if any of:

- **No executable fills.** ONE_TICK_BEHIND records zero modelled fills
  across 15 consecutive settled windows. Conclusion: not executable at
  this depth.
- **Controller errors.** Any `controller_error:*` attributable to the new
  arm.
- **Money-path disturbance.** Any change in the money path's observer
  counts, any live order, any exposure above zero, or any limit change.
  (The arm cannot cause these; the condition exists so that the comparison
  is abandoned rather than explained if they occur.)
- **State discontinuity** in the new arm that is not reported by the
  `boot` block.

There is **no early stop for a favourable result.** Stopping when ahead is
how a noisy sample becomes a claim.

## 9. Threshold-selection ban

The offset (one tick), the window count (40), the time cap (12 hours), the
abstention floor (25%), and every metric and criterion above are fixed by
this file. None may be re-chosen after seeing results.
