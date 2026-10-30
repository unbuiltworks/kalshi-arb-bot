# The smallest next bounded live experiment

**Aim: complete a profitable pair, and bound the residual when only one leg
fills.** Proposal only — nothing here is configured or armed.

---

## The arithmetic that should drive it

For maker quotes `d` ticks behind each touch, with `own(UP) = bid` and
`own(DOWN) = 1 − ask`:

```
pair_own_cost = (bid − d·t) + (1 − ask − d·t)
              = 1 − spread − 2·d·t
```

Against the 0.96 cap, at a 1-tick spread (the normal BTC15m book):

| | `d = 0` (at touch) | `d = 1` | `d = 2` |
|---|---|---|---|
| 1-tick spread | 0.99 ✗ | 0.97 ✗ | **0.95 ✓** |
| 2-tick spread | 0.98 ✗ | **0.96 ✓** | **0.94 ✓** |
| 4-tick spread | **0.96 ✓** | **0.94 ✓** | **0.92 ✓** |

**A profitable pair requires BOTH legs to fill as makers behind the touch.**
One leg behind the touch and the other at it is 0.97 — refused, correctly.

## Why Campaign 10 could not have completed a pair

This is the finding, and it is structural rather than bad luck.

The DOWN leg filled at own 0.38, leaving 0.58 of room under the cap. The UP
complement then had to be quoted against a book that had *already moved* —
moving is why the first leg filled — and its 2-ticks-behind quote was own
0.60. Pair 0.98 > 0.96, refused. One fill, zero pairs, residual carried to
settlement, −$0.7258.

The binding constraint is `max_live_orders: 1`, confirmed in this boot's
`L5_LIVE_START`. **With one outstanding order you can never hold both legs
behind the touch at once, so you can never collect the `2·d·t` discount on
both sides.** As currently configured the strategy cannot complete a pair at
≤0.96 except when the spread happens to reach 4 ticks. Sequential
open-then-complete is the wrong shape for this cap.

## Step 1 — a free shadow readout first (no money)

`L5_TWO_SIDED_SHADOW` already parks each window and scores it against the
venue's own binary resolution (`two_sided_ledger`, settled independently of
live exposure). Before spending anything, read its accumulated scored
windows for the one number that decides whether the live probe is worth
running:

> **How often do BOTH behind-touch legs fill within one 15-minute window,
> and what did the completed pairs earn after fees?**

This costs nothing and we have never actually read it for this question. If
the joint fill rate is ~0, the live probe would burn three windows learning
what the shadow already knows, and the right next move is a quoting change
(depth, timing, or market selection) rather than a live probe.

**Do step 2 only if the shadow shows a non-trivial joint fill rate.**

## Step 2 — the bounded live probe

### The one change

Enable the existing `L5_TS_TWO_ORDER_PAIR`. It is already built, off by
default, and is **not** `max_live_orders = 2` — its own comment is explicit
that two independent first legs sharing one cash envelope is the 1215z
failure mode. It grants a second maker slot only to an order that is:

- the **opposite** side (a same-side order is an EXTEND, refused),
- in the **same market**,
- in the **same round**, with the resting side claimed and this side not,
- when **exactly one** order is live,

with six named fail-closed refusals. No new code, no new service.

### Configuration

| | |
|---|---|
| Markets | 1 |
| Windows | 3 |
| Orders per round | 2 — one per side, placed in the same tick |
| Depth | 2 ticks behind each touch, both legs |
| Size | the clip already traded live, ~1.9 contracts ≈ $0.73/leg, ≈$1.45/pair |
| Execution | `MAKER_ONLY`, defensive crossing **disabled** |
| Exposure | $20 (unchanged) |
| Daily realized loss halt | $5 (unchanged) |
| Per-market worst-case deficit | $3.00 (unchanged) |
| Campaign envelope | $9 (unchanged) |

### The gate that makes it a pair trade rather than two bets

**Refuse to place either leg unless the pair cost at the two intended quotes
is already ≤ 0.96.** The cost is fully known from the book *before* either
order rests — that is the whole advantage of placing both at once, and it is
what the sequential flow could never have. If the book is 1 tick wide and
`d = 2`, the pair is 0.95 and both legs go in; otherwise neither does.

### Residual limiting — what the owner specifically asked about

Worst case per round is **one leg's own cost**, not $1.00/share: the filled
leg redeems 1 or 0, so the loss is capped by what we paid for the unmatched
leg (≈$0.73 at this clip). Existing controls already bound it — the $3.00
per-market worst-case deficit, the residual ceiling (absolute cap 3
contracts, `which_binds: ABSOLUTE`), and `L5_TS_CANCEL_MARGIN_S`.

Two rules for the unfilled leg:

1. **Leave it resting** while it can still complete the pair inside the cap.
   Cancelling a leg that is still economic is how Campaign 10 ended with
   residual and no pair.
2. **Cancel at the margin**, not on a timer, and never replace it at a price
   that pushes the pair above 0.96 — that converts a pair trade into a
   directional bet, which the evidence says we have no skill at.

### Success criterion

**One completed pair, at a fee-safe cost ≤0.96, with settlement confirming
$1.00 redemption on the matched quantity.** At 0.95 and this clip that is
about **+$0.0955** per pair, with $0 maker fees (confirmed on a real fill).

Trivial in size, and that is deliberate: the experiment proves the **sign**
of the pair economics on real fills, which this programme has never done.
Frequency and size come after, in that order.

## The honest caveat

Resting 2 ticks behind the touch on both sides means neither leg is near the
front of its queue, so **the likely outcome of any single window is no fills
at all** — and there is an adverse-selection asymmetry: the leg that fills is
the one the market moved against, so single-leg fills will outnumber pairs.
That asymmetry, not the cap, is the real economic risk, and bounding it is
what the residual rules above are for.

So the probe's actual question is the joint fill rate — which is exactly why
**step 1 comes first**, and why three windows may well produce zero pairs and
still be a valid result rather than a failure.

## What this does not propose

No wider pair cap, no taker crossing (θ = 0.0695 against a pair that costs
0.99–1.00 at the touch is structurally loss-making here), no extra
concurrency, no larger size, no new market series, and not the standing agent
policy.
