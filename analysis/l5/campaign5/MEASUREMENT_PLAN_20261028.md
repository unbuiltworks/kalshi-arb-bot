# What the next campaign must measure, and the ceiling it runs into

Baseline to beat: the four-window campaign of 2026-10-28 — **11 submitted
orders, 8 fills, +$0.7083 confirmed settled net** ($0.177 per window).

## The five questions, and how each is answered from records

**1. Can we repeatedly complete multiple profitable pairs in one window?**
Per window, count completed pairs with each pair's `up_own + down_own` cost
and its settled redemption. A pair is counted only once both legs are
venue-confirmed filled. The target to beat is 1 pair/window.

**2. Does the corrected retirement logic increase actual completed pairs,
not merely attempts?** Record, per window: `TWO_SIDED_ROUND` retirements
with their `plan_unexecutable` reason, rounds reached, orders submitted,
fills, and completed pairs. The deadlock's signature —
`retirement_blocked_by: LEG_HOLDS_*` on a **zero-residual** book — must now
be absent. **Order count rising while completed pairs do not is a failure,
not progress**: that is precisely what the previous campaign did.

**3. How often do we strand inventory, and what does it cost?** Per
stranded leg: side, own price, quantity, the complement prices refused and
why (`COMPLEMENT_PAIR_ABOVE_CAP` with the pair cost), and the settled loss.
Report strands per window and dollars per strand.

**4. Confirmed settled net P&L after all costs.** Settlement-only, with
fees and any credited rebate on separate lines, never netted into trading
P&L. Labels: submitted → filled → settled → realized.

**5. How closely do decisions match L5's?** Per decision, the recorded
`l5_price` and `l5_size` beside our own side, price, size and timestamp,
plus the inventory state each was taken from. Report side agreement, price
distance in ticks, size ratio, and timing lag — not aggregate P&L, which
can agree while behaviour does not.

## The ceiling this campaign will hit, measured in advance

At the observed pair economics and the approved envelope:

| | |
|---|---|
| edge per contract at pair 0.94 | $0.06 |
| gross per pair, 1.87 contracts | $0.1122 |
| **net per pair** after 2 × $0.01 fees | **$0.0922** |
| cash each pair commits (never released pre-settlement) | $1.7578 |
| pairs permitted by 4 round allocations | 4 → $0.3688 |
| pairs permitted by 8 max rounds | 8 → $0.7376 |
| pairs fundable by $12 under NO_RECYCLING | 6 → $0.5532 |

**Binding constraint: the 4 round allocations. Structural ceiling with every
authorized round completing a perfect pair: $0.3688 per window — $35.40 per
day across 96 windows.** The $5/window target is **13.6× above** that
ceiling, and $480/day is 13.6× above what this envelope can produce even
with flawless execution.

The previous campaign returned $0.177/window, about **48% of that ceiling**.
So the realistic best case for this campaign is roughly **$0.37/window**,
and the honest success criterion is *approaching the ceiling*, not
approaching $5.

**And one stranded leg costs $1.7578 — 19.1 completed pairs of net edge.**
A single strand in a four-window campaign erases more than the entire
campaign's ceiling.

## The single biggest remaining obstacle

Not throughput. **The approved envelope cannot produce $5/window, and the
strand cost dwarfs the pair edge.** Two numbers say it: the ceiling is
$0.37/window against a $5 target, and one strand costs 19 pairs.

The deadlock fix was still necessary — it had made 3 of the 4 allocations
unreachable, so most of that $0.37 was unavailable — but closing the gap to
$480/day cannot come from more orders inside these limits. It requires one
or more of:

* **more capital per window.** NO_RECYCLING is not a bug to remove: a
  BTC15m pair settles after the window expires, so cash genuinely cannot
  recycle intra-window. More pairs per window therefore means more cash per
  window, which is an owner risk decision and not an engineering one;
* **a materially better pair cost.** Every $0.01 off the pair adds $0.0187
  per pair at this size — a pair at 0.90 instead of 0.94 nearly doubles the
  net edge. This is the highest-leverage research direction and it is about
  entry selection and patience, not speed;
* **concurrency.** One market at a time is the current restriction; the
  per-window ceiling scales roughly linearly with concurrent markets, and
  the discipline order is PATIENCE → FREQUENCY → SIZE → CONCURRENCY, so
  this is last;
* **eliminating strands**, which is worth more than any throughput gain
  until the strand rate is near zero.

This campaign's job is to establish the strand rate and the achievable
fraction of the $0.37 ceiling. Those two numbers decide which of the levers
above is worth the next engineering effort.
