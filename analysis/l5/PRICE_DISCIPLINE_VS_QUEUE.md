# Price discipline vs fill probability: the cut is degenerate, and the mechanism is exact

Owner: *"cut our proposals by pair-cost target and queue state. Specifically
test ≤0.96, 0.96–0.98, 0.98–0.99, 0.99–1.00, and >1.00 separately... does
relaxing our price target toward L5's ~0.988 median increase completed pairs
enough to improve expected realized economics?"*

**Headline: the requested cut cannot be run — our pair target does not vary, it
is pinned at 0.98 on 120 of 121 proposals. But the queue-aware rows answer the
underlying question exactly, and the answer is arithmetic: our 0.98 target is
what puts us three ticks behind roughly 200,000 shares.**

---

## 1. Why the bucketed cut is degenerate

From 121 per-proposal rows emitted since the ladder deploy:

```
implied_pair_cost_at_post   0.98 on 120 rows, 0.97 on 1
max_pair_cost               0.98 on 121 rows
```

The complement price is computed as `min(ceiling − avg_cost, hard_bound,
opp_ask − tick)`, so the pair cost is **pinned at the cap by construction**.
Brett's five buckets would hold ~100% of proposals in one (0.96–0.98) and
**zero** in 0.98–0.99, 0.99–1.00 or >1.00.

This is the third pre-registered discriminator in this programme to turn out
constant by policy or by arithmetic — after the guaranteed-floor metric and the
entry-pair identity. The pattern is worth naming: **we keep proposing to measure
variation in quantities our own gates hold fixed.**

## 2. The mechanism is exact, and it needs no buckets

Target, distance and queue are linked by arithmetic, not by correlation. For
each proposal the touch price is recoverable from `complement_px` and
`ticks_from_touch`, so the pair cost we *would* have paid to join the touch is
computable directly.

Over the 40 rows carrying both queue data and a surplus cost:

```
pair cost at OUR price     median 0.9800   (pinned target)
pair cost AT THE TOUCH     median 0.9950   range 0.9900-0.9950

share of touch-pairs <= our 0.98 cap        0.0%
share <= L5's 0.9876 median                 0.0%
share <= 1.00                             100.0%
```

Live examples, all NFL:

| market | our px | touch | pair @ ours | pair @ touch | ticks back | **queue ahead** |
|---|---:|---:|---:|---:|---:|---:|
| `aec-nfl-bal-ind` | 0.590 | 0.605 | 0.9800 | 0.9950 | −3 | **203,519** |
| `aec-nfl-chi-car` | 0.580 | 0.595 | 0.9800 | 0.9950 | −3 | **178,432** |
| `aec-nfl-atl-pit` | 0.680 | 0.695 | 0.9800 | 0.9950 | −3 | **143,537** |

**1.5 cents of pair discipline buys three ticks of distance, and three ticks
puts ~200,000 shares in front of us.** That is the owner's Zverev arithmetic,
measured on our own live proposals rather than inferred from a screenshot.

## 3. And it is the same identity as this morning

`pair at touch = 1 − spread`. These books run a 0.005 spread, so joining both
touches costs **0.995**, every time — which is why the column is near-constant
rather than distributed. Our 0.98 target demands **1.5 cents more edge than the
book has to give at the touch**.

So this is not "we are stricter than L5". It is stronger:

> **Our pair target is tighter than the spread allows at the front of the
> queue. Holding 0.98 in a half-cent-spread market makes standing at the touch
> arithmetically impossible.**

## 4. What that does and does not answer

**Answered:** why our complements sit 3–4 ticks out and fill at 0.74%. Not
judgement, not market thinness — the price target mechanically places them
there.

**Answered:** adopting L5's 0.9876 median would **not** reach the touch in these
markets either. It closes maybe one tick of the three. The touch needs 0.995.

**NOT answered, and not estimable from these rows:** whether a relaxed target
improves *realized economics*. That needs completion rates and residual
outcomes at targets we have never quoted, and no observational cut of a pinned
variable can supply them.

## 5. The only honest way to answer the economics, and its cost

Price the complement at **several targets simultaneously** — say 0.98, 0.985,
0.99, 0.995 — and follow each forward against the same tape with the same
validated trade-through rule. The execution shadow already does exactly this
for one price; it would do it for N. Still read-only, still no orders, still no
R5F change.

That is one more deployment of the same service. It is the only design that
produces the buckets the owner asked for, because it *creates* the variation
the policy currently forbids.

## 6. Sample caveats, stated plainly

n = 40 proposal-observations, heavily NFL, with the same few markets appearing
repeatedly (`bal-ind` four times). This is a **mechanism demonstration**, not a
population estimate. The mechanism is arithmetic and does not depend on n; the
*prevalence* of 200k queues across sports does, and is not established here.

81 of the 121 rows carry no queue data — every one of them is a proposal
created before the ladder deploy, still being followed. Coverage rises to ~100%
as those age out.

---

*Read-only. No live change, no ceiling raised, R5F off.*
