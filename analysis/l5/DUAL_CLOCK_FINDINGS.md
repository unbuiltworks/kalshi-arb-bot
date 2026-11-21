# P1 — what the 15-second recovery actually buys

The chain carries L5's fills a measured **15.16 s** ahead of the venue's own
publication (n=73, ahead on 73/73). That is a fact about **observation**. This
is what it is worth in **execution**.

Same frozen intent, two observation clocks. Same anchor, same ceiling, same
quantity, no model change — only the moment we could first act on it differs.
Arm B is priced from a book recorded **before** arm A's, so nothing in it is
hindsight; where no such book exists within 8 s, the row is UNKNOWN rather than
approximated.

**The chain observer remains UNPROMOTED.** This measures what promoting it
would buy and promotes nothing.

---

## The answer, at 788 compared placements

| | feed clock (A) | chain clock (B) |
|---|---|---|
| intent still valid when observable | **90.9 %** | **100 %** |
| maker placement possible | **54.8 %** | **63.5 %** |
| L5's price reachable | **69.2 %** | **69.2 %** |
| **evidenced fill the other clock missed** | **0** | **0** |

Median recovery **13.64 s**.

### What it buys

1. **One intent in ten, rescued from decay.** 9.1 % of intents have already
   expired by the time the feed publishes them; none has under the chain clock.
   Real, and much smaller than "83 % decay in 30 s" might suggest — because
   most publication lag lands well inside the 30 s window.
2. **About nine points of placeability**, 54.8 % → 63.5 %. But it is not a
   free gain: **23 rows are placeable only under the chain clock and 14 only
   under the feed clock.** The earlier book is not uniformly better; sometimes
   the bid we needed had not arrived yet. The net is +9, not +23.

### What it does not buy

3. **Reachability is identical — 69.2 % in both arms.** Being 13 s earlier does
   not improve our ability to rest at or under L5's own achieved price. It
   changes whether there is a bid to join at all, not what that bid costs.
4. **Not one additional evidenced fill.** `filled_only_under_chain: 0`,
   `filled_only_under_feed: 0`. The fill comparison is still thin — 70 rows
   NOT_DETERMINABLE, 31 UNKNOWN — so this is not yet a settled negative. But
   the honest current reading is: **the clock has not been shown to buy a
   single fill.**

## What this means for priority

The blocker ranking established earlier was intent decay → observation lag →
price → availability. This measures the second item and finds it worth about
one intent in ten and nine points of placeability, with **no demonstrated
effect on fills**.

Against the P2 finding — **zero completed pairs across every position, 100 %
directional residual, `realized_shadow` −$102.77** — the conclusion is
straightforward:

> **A faster route to an unpaired position is still an unpaired position.**

Promoting the chain observer is worth doing when its gate passes, on its own
merits, for the shadow observation clock. It is **not** the thing that unblocks
the economics, and it should not be prioritised as though it were.

## Coverage caveat

Arm B is priced on 104 of 788 rows (13 %). The rest have no recorded book
within 8 s of the chain-clock moment, because placement-time reads only happen
when L5 acts. The scheduled 30/60/120 s re-reads now record the rest side as
well and are starting to land (0 → 90 usable snapshots in ten minutes), so this
fraction should rise for data collected from here on. **The percentages above
are over the priced subset and will move as it grows.**

## Provenance

- `analysis/l5/dual_clock/study.py` — the comparison, reported from the service every 600 s
- `tests/test_l5_dual_clock.py` — 29 tests
- source: `l5-exec-shadow` deployment `b238e8ba`, 2026-09-15T17:19:54Z
