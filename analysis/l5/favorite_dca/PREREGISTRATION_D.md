# FAVORITE_TRAIL_NO_TAKER_SEED (Variants D0 and D1) — preregistration

Committed before any D number existed. **C is preserved unchanged as the
control** and is re-run by the same code path, which must reproduce its
published figures exactly or the comparison is void.

The prepared C live probe is **not executed**. C is shadow-negative and a
plumbing probe does not justify live exposure; that stands.

No live orders. No new threshold. No parameter search: D0 and D1 are two
preregistered subreports, not a grid.

## The hypothesis being falsified

C decomposed into a matched/two-sided component of **−$19.837** and a
directional component of **+$15.449**, with fees of **−$6.124**, over 28
windows. The mandatory two-sided taker seed is what buys the matched
component, and it pays two half-spreads plus two taker fees in every window
for inventory that can only ever return $1.00.

> **D removes only the seed.** If the directional loop survives the removal
> substantially intact, D's net is positive. If the directional component was
> an artifact of already holding both sides — for example because the seed is
> what made an early extension possible, or because the exits were selling
> shares the seed had provided — D's directional component will fall with it.

That is the falsifiable claim, and either answer is a result.

## D0 — favorite trail, no opposite inventory

Identical to C in **every** respect except the seed:

- cadence 60 s — unchanged;
- favorite from the executable mid, `> 0.50` / `< 0.50`, tie = no action —
  unchanged;
- "strengthening" = favored side's own mid strictly above its mid at the
  previous evaluation — unchanged;
- allocation $1 taker at the touch, capped by displayed depth — unchanged;
- the reversal rule is C's, verbatim: trigger when the favored mid falls
  below `peak − (spread_at_peak + 2 × taker_fee(peak))`, evaluated on every
  book update — unchanged, and its magnitude is **not** re-derived or
  re-tuned;
- on reversal: stop extending, reduce the directional residual to zero at
  contemporaneous executable prices across as many books as depth requires,
  never unwinding matched inventory — unchanged;
- released capital is recycled — unchanged;
- on a flip the new favorite becomes eligible and the peak tracker resets —
  unchanged.

**The only change:** no $1 UP + $1 DOWN taker seed at the first mark. The
first position of a window is therefore the first extension, which C's
existing logic already governs — an episode opens on the first strengthening
read. There is no new entry rule and none is invented.

## D1 — D0 plus passive opposite rest

D0's controller exactly, plus the **already-defined** passive opposite-side
quote from Variant B, carried over without modification:

- reservation `= 1.00 − avg cost of the favored side − taker fee per share
  paid`, with the modelled maker rebate excluded;
- post-only at `min(reservation, opposite_ask − 0.01)`, suppressed when the
  reservation is below $0.01 or no favored inventory exists;
- $1 notional, one order, re-evaluated each 60 s period, following the
  non-favored side across flips;
- **no touch = fill**: the order fills only once its price is the touch, with
  queue-ahead taken from the depth displayed there, depleted only by trades
  consuming our side, and zeroed by a print strictly through our price;
- a price change of ≥ one tick is a cancel and a new order, losing queue
  position.

**No forced taker hedge.** Opposite inventory may only ever arrive passively.

A known property of that frozen rule is carried over rather than fixed:
fixed $1 notional at a reservation price near $0.01–$0.05 buys a very large
share count, which is what made B a longshot accumulator. D1 is expected to
inherit it, and the share-count and residual consequences will be reported.
Changing it would be a new threshold, which this preregistration forbids.

## The primary decomposition — an identity, not a model

For each variant and window:

```
directional realized P&L
  + matched inventory economics
  + settlement residual
  + verified compensation
  − fees
  = net
```

with each term defined so that the identity holds exactly:

- **directional realized P&L** = gross exit proceeds − cost basis of the
  shares exited (average cost of that side at the moment of exit);
- **matched inventory economics** = terminal matched shares ×
  `(1 − avg UP cost − avg DOWN cost)`;
- **settlement residual** = settlement value of the terminal residual −
  its cost basis;
- **verified compensation** = $0. D0 has no maker fill at all; D1's maker
  rebate is modelled and reported on its own line, never inside net;
- **fees** = every taker fee paid, on entries and exits.

The reconciliation residual is printed per window and must be 0 to six
decimals. A non-zero residual voids the decomposition rather than being
absorbed into the directional line.

## Reporting, fixed now

**Median is reported as prominently as mean**, together with:
leave-one-best-window-out net, positive-window fraction, worst window, max
drawdown, peak capital, capital turnover, max and terminal residual, and net
per window — for D0, D1 and frozen C side by side on the same 28 windows.

## The scaling gate

Everything is computed at the **existing $1 scale**. **No capital envelope
and no $5 projection is computed unless the $1 economics are positive
without dependence on a tail window** — concretely: positive mean AND
positive median AND positive after removing the single best window. If any
of those three fails, the envelope study is not run and that is reported as
the result.
