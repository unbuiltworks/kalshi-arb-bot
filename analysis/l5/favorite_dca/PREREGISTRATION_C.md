# TWO_SIDED_FAVORITE_TRAIL (Variant C) — preregistration

Frozen before any Variant C number existed. Scored on the **same 28
untouched windows** as Variants A, Am and B; those three are not re-run, not
re-tuned and not re-gated. No threshold sweep, no retrospective choice of a
dip, no claim of realized profit.

## Mechanics

1. **Seed, bounded, both sides.** At the first evaluation mark (window start
   + 60 s) buy **$1 UP and $1 DOWN**, taker, at the contemporaneous touch,
   capped by displayed depth. That is the whole two-sided establishment: it
   is bounded by construction and never repeated.
2. **Extend the favored side**, $1 taker, once per 60 s — but only while
   condition 3 holds.
3. **"Strengthening" is defined as:** the favored side's own mid is strictly
   above its mid at the previous evaluation. Flat or down ⇒ no extension this
   period (recorded as a withheld extension, not a skip).
4. **The reversal rule — exactly one, and derived, not searched.**

   Let `peak` be the running maximum of the favored side's mid since the
   current extension episode began, and let `spread_at_peak` be the
   bid-ask spread observed when that peak was set. The reversal triggers
   when the favored side's mid falls below

   ```
   peak  -  (spread_at_peak + taker_fee_in + taker_fee_out)
   ```

   where both fees are `src/pmus_fees.taker_fee` per share at the peak price.
   The magnitude is therefore **the round-trip cost of having been in the
   position at all** — the move that must be given back before the trade
   stops having paid for itself. No trailing distance is chosen, and none is
   varied.

   The reversal is evaluated on **every book update**, not only at the 60 s
   marks, because it is a risk control and a dip between marks is still a
   dip. Extensions stay strictly once per 60 s.
5. **On reversal:** stop extending, and reduce the **directional residual to
   zero** at contemporaneous executable prices — taker, capped by displayed
   depth, continuing across subsequent books until flat. Any shares that
   depth prevents exiting on the first book are reported as **cleanup**.
   **Matched inventory is preserved** and never unwound: a matched share pays
   exactly $1.00 at settlement, so unwinding it at `1 - spread` would
   destroy value.
6. **After a flip** the newly favored outcome becomes the side eligible for
   extensions, and the peak tracker resets. The peak tracker also resets
   after an exit, so a new episode starts from the price it starts from.

All holdings are one aggregate portfolio. No purchase is forced into a pair.

## Accounting, fixed now

Net is a cash identity, not a model: `net = cash in − cash out`, where cash
out is every (price × shares + taker fee) and cash in is every exit's
(proceeds − taker fee) plus the winning side's shares × $1.00 at settlement.

Reported separately: **matched-pair economics** (final matched shares ×
`1 − (avg UP cost + avg DOWN cost)`), **directional trading P&L** (the
reconciling remainder, so that matched + directional + fees ties exactly to
net — the reconciliation line is printed and any discrepancy is reported, not
absorbed), **maker compensation** (there is none in C: every C fill is a
taker fill, so this line is structurally $0 rather than unverified),
**fees**, **cleanup**, **maximum residual**, **maximum capital**,
**drawdown**, **settlement P&L** and **net per window**.

For every cash-out, the **mark-to-executable P&L immediately before and
immediately after** the exit is recorded, together with the shares and prices
of the exit, so it can be checked whether the reversal captured anything or
merely moved value between accounting lines. A cash-out that leaves
before ≈ after has changed presentation, not value, and will be reported that
way.

## Live-probe gate

A bounded q1 live-probe plan is **prepared only if** the shadow mechanics
survive with no semantic or accounting defect — reconciliation ties, no
touch=fill anywhere, no exit priced better than the contemporaneous
executable price, and no negative share counts. It is **not executed**
without explicit authorization, and preparing it is not authorization.
