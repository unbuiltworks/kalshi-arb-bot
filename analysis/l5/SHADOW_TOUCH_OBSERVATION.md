# The shadow observation already exists, and it points away from spread

Owner 2026-09-11: *"start the shadow observation... record whether both maker
touches actually trade within a bounded window... No modeled P&L. No live
orders. No new strategy deployment."*

**It is already running.** `pmus-shadow-breadth` (deployment `6b905209`,
01:55Z today) follows every complement proposal forward through the live tape
with the fill rule validated against real PMUS fills — `WOULD_POST →
WOULD_FILL → VIRTUAL_MATCHED`, and a trade must come *through* the resting
price in the hitting direction to count. Nothing is modelled and no order is
placed.

As of cycle at 17:25:52Z: **691 proposals, 555 resolved, 18 VIRTUAL_MATCHED.**

---

## 1. Probability band does not separate fills. At all.

The predictor split over 555 resolved / 18 matched:

| predictor | matched median | no-fill median | separation |
|---|---:|---:|---:|
| **flow_usd_per_min** | **217.11** | **16.44** | **+200.67  (13×)** |
| mid_stdev | 0.0545 | 0.0007 | +0.0538  (78×) |
| mid_range | 0.200 | 0.005 | +0.195  (40×) |
| spread | 0.010 | 0.005 | +0.005  (2×) |
| **mid** | **0.5150** | **0.5175** | **−0.0025  (none)** |
| **band_position** | **0.530** | **0.535** | **−0.005  (none)** |

**A complement that fills and one that does not sit at the same place in the
book** — 51.5% against 51.75% mid. Being near 50/50 is not what distinguishes
them, because in this cohort essentially everything already is.

**Spread separates, but weakly** — 2×, against flow's 13× and movement's 40–78×.

## 2. What actually decides it: distance from the touch

| distance (ticks) | n | fill ≤300s | fill ≤1800s | median flow | median spread |
|---|---:|---:|---:|---:|---:|
| 0–1 | 4 | 0.0% | 50.0% | 467.6 | 0.010 |
| **2** | **21** | **47.6%** | **76.2%** | 399.0 | 0.010 |
| **3–4** | **407** | **0.74%** | **1.23%** | 12.6 | 0.005 |
| 5–8 | 23 | 4.35% | 26.1% | 7.0 | 0.010 |
| >8 | 51 | 5.88% | 23.5% | 111.1 | 0.020 |

Two ticks from the touch fills **47.6%** in five minutes. Three to four ticks
fills **0.74%** — a **64× cliff** across one tick of distance.

**And 407 of 506 measured proposals sit in the 3–4 band.** The same pathology
the entry study found: the engine concentrates its activity in the one place
the mechanism does not work.

NFL stratified, the only usable stratum: near (≤2 ticks) **40.0%** fill
against far (≥4 ticks) **1.75%**, contrast 0.3825, n = 25 near / 400 far.

## 3. This argues against the wide-spread hypothesis, and says why

The band × spread grid showed the >5¢ cells have the *arithmetic* to pay. This
shadow measures whether anything would actually **fill** there, and the three
pieces fit together badly for the hypothesis:

1. Fills track **flow and movement**, not spread. Matched proposals had 13×
   the flow and 40–78× the price movement.
2. Wide spreads come with **82× thinner books** (7500 → 91 at the touch,
   measured in §5 of the band × spread findings). Thin books are low-flow
   books.
3. The one comparatively wide band this shadow does see — >8 ticks, median
   spread 0.020 — fills **5.88%** at 300s, against 47.6% for the 2-tick band
   at a 0.010 spread.

A wide spread means the *prize* is larger. It does not mean anyone will trade
against you, and the prize is only collected on a fill.

## 4. What this shadow cannot do, and what that would cost

**It cannot directly test the 35–65 × spread >5¢ cell**, because its watch
list is **flow-ranked** — it is already selecting high-flow markets, and its
observed spreads are 0.005–0.02. The wide-spread cell is largely outside its
cohort by construction.

Watching those 54 markets specifically means changing what the service
watches. That is a configuration change to a running service — **a
deployment** — which is exactly what is currently ruled out. It is a
read-only, no-order-path service, so the change carries no market risk; the
cost is the deployment itself and the owner's call.

**No deployment was made for this analysis.** Everything above came from logs
of a service that was already running.

## 5. Against the owner's decision bar

> *"if those wide-spread balanced markets show two-sided touch activity often
> enough that one-sided incidence plausibly stays below roughly 15–20%, then
> we have a real candidate."*

Read directly: the shadow's overall `p_completion_given_first_fill` is
**3.24%**, implying one-sided incidence near **97%** — far outside the bar.
But that number is dominated by the 3–4 tick dead zone where 80% of proposals
sit, so it measures *where we post*, not *where we could post*.

The 2-tick band reaches **76.2% completion within 30 minutes**, implying
one-sided incidence near **24%** — close to the bar, and achieved at a **1¢**
spread in a **high-flow** market, not a wide one.

**So the candidate this evidence supports is not "wide spreads near 50/50". It
is "post within two ticks of the touch, in markets with flow".** That is a
different hypothesis from the one we set out to test, it is measured rather
than assumed, and it needs its own pre-registration before anything is built
on it.

---

*Read-only, from an already-running service's logs. No deployment, no orders,
no live restart. R5F remains off.*
