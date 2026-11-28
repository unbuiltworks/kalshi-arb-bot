# First-leg-to-complement execution analysis

Source: `L5_FILL_BOOK_EVIDENCE`, `L5_BOOK_COMPLETION_TRIGGER`,
`L5_TS_COMPLETION` and the `stats` refusal counters from the Stage A
campaign (2026-10-30, windows 2115z/2130z/2145z/2200z, deployment
`9b43b312` on commit `0304fbe8`). Every book row quoted below is a
recorded frame, not a reconstruction.

## 1. Every first-leg fill, with both resting quotes and distance from touch

`first_leg_own` is the own-price basis of the leg we were left holding.
`at-touch pair` is `pair_if_maker` (join the touch); `2-tick pair` is
`pair_if_maker_behind_touch`, **the price the deployed engine actually
quotes**; `taker pair` is `pair_if_taker_walk`.

| # | window | time | held | first_leg_own | book at fill | spread | at-touch pair | 2-tick pair | taker pair | clears 0.96? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2115z | 21:18:55 | DOWN | 0.21 | 0.79 / 0.80 | 0.01 | 1.0000 | 0.9800 | 1.0100 | no |
| 2 | 2115z | 21:19:52 | UP | 0.78 | 0.72 / 0.74 | 0.02 | 1.0400 | 1.0200 | — | no |
| 3 | 2115z | 21:20:01 | UP | 0.67 | 0.68 / 0.69 | 0.01 | 0.9800 | **0.9600** | 0.9900 | **exactly at** |
| 4 | 2115z | 21:20:41 | DOWN | 0.27 | 0.74 / 0.75 | 0.01 | 1.0100 | 0.9900 | — | no |
| 5 | 2115z | 21:20:52 | DOWN | 0.23 | 0.80 / 0.81 | 0.01 | 1.0300 | 1.0100 | 1.0400 | no |
| 6 | 2130z | 21:30:32 | UP | 0.46 | 0.44 / 0.45 | 0.01 | 1.0100 | 0.9900 | 1.0200 | no |
| 7 | 2145z | 21:45:30 | DOWN | 0.45 | 0.54 / 0.55 | 0.01 | 0.9900 | 0.9700 | 1.0000 | no |
| 8 | 2200z | 22:00:20 | DOWN | 0.33 | 0.69 / 0.70 | 0.01 | 1.0200 | 1.0000 | 1.0300 | no |
| | | | | | **mean** | **0.011** | **1.0100** | **0.9900** | — | **0 of 8 below** |

**At the moment of every one of the 8 first-leg fills, the complement pair
cost at the engine's own quoting depth was at or above the 0.96 opening
cap** — 7 of 8 strictly above, the 8th exactly at 0.9600 with
`margin_to_ceiling 0.0000`.

## 2. Does the opening policy systematically expose us to adverse selection?

**Yes, and the mechanism is structural, not a tuning accident.**

A resting bid below the touch fills only when the price falls to it. The
moment it fills, the complement has become dearer by about the same amount
our leg became cheaper, because

```
pair = P_first_leg + P_complement
```

and the book anchors the two-sided total near `1 - spread`. Buying both
legs at the touch therefore costs ~0.99 at the observed 0.01 spread, and
resting deeper does **not** cheapen the pair — it only changes which side
is cheap. Quoting `d` ticks behind the touch improves the pair by exactly
`2 * d * tick` (the table shows exactly 0.02 between the at-touch and
2-tick columns), but the market only reaches a deeper quote after moving
against the complement.

The consequence: the 0.96 cap is reachable only if **both** deep legs fill.
One-sided fills are the norm — 8 fills produced 3 stranded legs and only
2 pairs — and after a one-sided fill no maker completion clears 0.96.

Harmless for a matched pair; pure adverse selection for an unmatched leg.

## 3. Depth comparison: at-touch vs 1 tick vs 2 ticks, on recorded books

Derived from the same 8 frames. The 1-tick column is the at-touch pair less
one tick, which is exact arithmetic on the recorded rungs.

| opening depth | mean pair at first-leg fill | pairs ≤ 1.00 | pairs ≤ 0.96 | locked edge per contract |
|---|---|---|---|---|
| at touch (0 ticks) | 1.0100 | 3 of 8 | 0 of 8 | **negative** |
| 1 tick | 1.0000 | 5 of 8 | 0 of 8 | 0.0000 |
| 2 ticks (deployed) | 0.9900 | 7 of 8 | 1 of 8 | +0.0100 |

**This comparison cannot be settled on P&L, and it would be dishonest to
claim otherwise.** It ranks the three depths only on *pair cost*, which is
exactly computable from the recorded book. The other half — fill
probability — is **unmeasurable on this venue**: PMUS publishes no print
endpoint (`/trades`, `/history`, `/ohlc`, `/candles`, `/price-history` all
404), so joint maker-fill probability cannot be estimated from public data.

Queue uncertainty makes this concrete. Displayed size ahead of our quote,
as a multiple of the quantity we need:

| # | queue_ahead_multiple_of_need |
|---|---|
| 1 | 87× |
| 3 | 64× |
| 5 | 91× |
| 6 | 777× |
| 7 | 415× |
| 8 | 114× |

We would sit behind 64–777× our own size, and the venue publishes no
per-order queue position. **A touch at our price does not imply a fill**,
so no unfilled hypothetical in this document is treated as a fill.

Deeper quoting is therefore strictly better on pair cost and strictly worse
on fill probability by an unknown amount. The data cannot name an optimum.

## 4. Was a completed pair feasible below the cap at any point? Yes — and it was refused

Window 2145z is the decisive case. We held 2 DOWN at a 0.45 basis. The
market then moved decisively DOWN, collapsing UP to ~0.09–0.10, so the
completion became extremely cheap. The gate saw it correctly and approved
repeatedly, every ~6 s for roughly 12 minutes:

```
L5_BOOK_COMPLETION_TRIGGER  21:51:43
  held_side DOWN  need 2  side UP
  pair_own_cost 0.5300   breakeven_pair 0.9532
  within_preferred_target true   locked_pnl_usd 0.9400
  action COMPLETE  ok true  reason PAIR_BEATS_CARRY
```

And the executable route was priced, with both outcomes positive:

```
L5_TS_COMPLETION  21:51:49
  ok true   cross true   BUY 2 UP @ 0.1000   mode TAKER
  displayed_depth 126.0000
  cash_cost_usd 0.2200   taker_fee_usd 0.02
  worst_before -0.9400  ->  worst_after +0.8400
  worst_improvement_usd 1.7800
  both_positive_after true
  authorized_to_cross false   crossed false
```

A **$0.22 outlay, fee included, would have converted a -$0.94 worst case
into a +$0.84 gain in every outcome.** It was not acted on for one reason:
`authorized_to_cross: false`. Maker-only forbade it.

Window 2145z instead settled at **+$1.10 on an unhedged directional leg**.
That is a better number than +$0.84 and a strictly worse decision: the same
unhedged position was a -$0.94 loss if BTC had closed the other way, which
is precisely what happened in 2130z (-$0.943) and 2200z (-$0.610).

The maker route was not available here. To rest as a maker against a UP
book at 0.09/0.10 we would have had to bid 0.09 or lower; the engine's
computed completion price of 0.10 **took the ask**, so step 6 of
`src/l5_book_completion.py:252` refused it:

```
L5_BOOK_COMPLETION ... WOULD_CROSS
```

Run-wide refusal counters confirm this is the dominant preventable blocker,
not a one-off:

| outcome | count |
|---|---|
| `PAIR_BEATS_CARRY` (gate approved, within target) | 98 |
| `RISK_REDUCING_COMPLETION` (approved, 0.9532 < pair ≤ 1.00) | 14 |
| `PAIR_ABOVE_REDEMPTION` (refused, pair > 1.00 — correct) | 100 |
| **`WOULD_CROSS` (approved, then refused at placement)** | **41** |
| `ORDER_ALREADY_RESTING` | 8 |

The gate approved a completion **112** times. **41** of those were then
refused because the only executable price crossed the spread. Windows
2145z and 2200z submitted **2 orders each for the whole window** — the
opening pair and nothing else. No complement order was ever placed.

## 5. What L5 did in comparable inventory states

L5's behaviour in comparable states is **not establishable from public data
at the required resolution**, and this section does not pretend otherwise.
PMUS publishes no print endpoint, so L5's own fills, their maker/taker
character, and its queue positions are not observable. What the repository
does hold is L5's *action* feed, which records side/price/size but not
whether a given completion crossed.

What the feed does support, and `L5_OPERATING_MODEL.md` already records:
L5 works inventory incrementally and tolerates temporary imbalance rather
than flattening on a timer, and mature (August-era) L5 shows **late taker
completion** behaviour. That late-taker behaviour is deliberately locked on
this branch and remains locked: it is not source-confirmed for our account's
economics. So this analysis does **not** cite L5 as authority for crossing.
The case for a bounded crossing completion stands on our own recorded
`both_positive_after: true` arithmetic, or it does not stand at all.

## 6. The single highest-impact change

On this evidence the highest-impact change is **not** a different maker
quoting depth. Depth moves the pair by `2 * d * tick` and trades it against
an unmeasurable fill probability; it cannot fix a one-sided fill.

The highest-impact change is to permit a **bounded, risk-reducing taker
completion when, and only when, it makes every outcome positive** —
the exact condition the engine already computes and logs as
`both_positive_after`. In 2145z that was +$0.84 guaranteed for $0.22.

**This requires explicit owner authorization and is not being enabled
here.** Crossing has been withheld in every instruction so far, and
`authorized_to_cross` stays `false` until that changes. What is proposed,
for approval:

- gate on `both_positive_after == true` only — never on pair edge alone;
- cap the taker outlay per completion and per window;
- maker-first always: cross only after a maker completion has been resting
  and unfilled for a stated interval;
- keep the 1.00 pair line and every existing exposure bound untouched;
- settlement-only realized labelling, unchanged.

The one change implementable **within** the current maker-only mandate,
and the one worth doing behind a feature flag, is narrower: when the
completion gate approves but the computed price would cross, **reprice the
complement to the best maker-side price that still satisfies the pair
ceiling and rest it there**, instead of refusing and leaving nothing
resting. Today those 41 `WOULD_CROSS` refusals leave no order on the book
at all. Repricing gives the completion a chance to fill where it currently
has none — with the explicit caveat, which the queue figures above make
unavoidable, that **resting at a maker price does not guarantee a fill**,
and on a book like 2145z's it quite likely would not have filled.

Expected value of that narrower change is therefore positive but small and
unquantifiable from this data. The $0.84-for-$0.22 case is where the money
is, and it needs authorization, not code.

## 7. What must not be concluded

- 2145z's +$1.10 is **directional luck on an unhedged leg**, not evidence
  the policy works. The campaign's only profitable window was its least
  controlled one.
- The campaign's 2 completed pairs were both profitable (+$0.1246 of pair
  edge) and the window that produced them still **lost** -$0.2756, because
  a 1.74-contract stranded leg cost more than the matched book earned.
  **Pair edge is not P&L.**
- Nothing here is realized profit. Estimated rebates of $0.1059 were
  credited $0 (`UNVERIFIED_NO_VENUE_CREDIT_RECORD`) and are excluded
  throughout.
- No unfilled hypothetical in this document is counted as a fill.
