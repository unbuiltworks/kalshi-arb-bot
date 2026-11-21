# Probe 08 — the smallest treatment, proposed. Not armed.

**Nothing here is armed, deployed, or authorized.** This is a proposal.

## The one change

A single refusal in the `SAFETY_OVERLAY`:

    while the pinned market's residual is non-zero, refuse a maker order
    whose side would INCREASE |residual|.                                
    reason: IMBALANCE_INCREASE_SUPPRESSED

That is the whole treatment. It is the smallest of the three because it has
**zero free parameters** — no cap, no age, no threshold to select, so
nothing to overfit and nothing to argue about later. It is also the best
trading arm on both held-out days and the best on the fit day.

It refuses; it never invents a trade, never crosses, never chases, never
improves a price, and never opens a position the frozen rule did not choose.
The opening leg from flat is always allowed. The repairing side is always
allowed.

## What stays exactly as it is

* `L5_NATIVE 030d8bf` frozen. λ, sizing, side selection, timing, PMUS
  translation, quantity flooring, tick flooring, exact preview-echo gate,
  account pin `d51b44a3fa83`, identity binding, uncertainty quarantine,
  authoritative reconciliation — all unchanged.
* The envelope unchanged: **$5 per market, $5 total worst-case exposure,
  residual ≤ 10 shares, one concurrent market, $5 daily realized-loss halt,
  `max_live_orders` 8, worst case $5.1738.** This is a refusal, so it can
  only ever reduce exposure.
* Maker-only. No taker fallback. No `L5_ADAPTED_V1` beyond this one lever.
* The two execution fixes are already in the build and are not new to this
  probe.

## What Probe 08 is actually for, and it is not P&L

The largest threat to the out-of-sample result is that it does not transfer:

> `COMPLEMENT_REPAIR` by construction wants exactly the side the market is
> moving away from. In L5's record those fills are real because **he** got
> them. We would be resting on the harder side, and our fill rate there is
> unmeasured.

So the probe's **primary objective is fill rate by side-relative-to-residual**,
not profit. Record per order: `OPENING` or `REPAIR`, the residual at
submission, rest duration, and terminal state. The question to answer is:

> when we rest on the side that reduces our imbalance, do we get filled at
> anything like the rate we get filled on the side that increases it?

If repair-side fills are much rarer, the treatment buys a smaller residual by
producing far fewer pairs, and the L5 replay overstates it. That is a real
possible outcome and the probe should be able to say so.

## What to expect, stated in advance so it cannot be rationalised after

On Probe 07's exact stream this treatment takes **6 orders instead of 9** and
ends at **−$0.4923 instead of −$0.77180**. That is still a loss. One BTC15m
window of six fills cannot establish an expectancy and no single probe result
should be read as one — a go/no-go on expectancy needs repeated clean
windows, and the honest reading of Probe 08 is the fill-rate table.

## One tension, stated rather than buried

The standing instruction was not to modify the clone because L5 had a losing
period, but to use that period as an out-of-sample behavioural test. This
proposal leaves `L5_NATIVE` frozen and adds a refusal in the overlay, which
is the architecture that separation was built for — but the evidence base for
the refusal **is** L5's losing period. A rule that helps most when the
residual is bleeding may be worth less when it is not, and three days of one
trader are not a distribution. That is a reason to measure Probe 08 on fill
rate rather than to read its P&L as a verdict, and a reason to want the same
treatment re-scored on a period where L5 was making money before any scaling
is discussed.

## Explicitly excluded, with reasons

* **A pair-cost completion gate.** Measured, and it is the worst arm in the
  table: refusing an expensive completion does not undo the opening leg, it
  converts a slightly-bad pair into a wholly directional bet. Residual
  quantity doubles.
* **`RESIDUAL_CAP` and `RESIDUAL_AGE`.** Both work, both are dominated on
  trading net, and both need a threshold. Smaller change wins.
* **Any scaling.** Frequency before size, and neither before this is
  measured.
