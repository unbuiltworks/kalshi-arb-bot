# Parent orders, cluster units, and whether L5's inventory recycles

`analysis/l5/causal/cluster_size_forensics.py` · `cluster_size_v1.json` ·
prospective recorders running since 16:01/16:04Z

Three results, each measured rather than assumed. The first is a **negative**
that removes a planned data source; the second changes the unit of behavioral
modeling; the third is the sharpest venue contrast in the program so far.

## 1. The public OrderFilled archive does NOT contain L5 — verified

The plan was to use `wzsg/polymarket-orderfilled-v2` to escape the RPC
receipt-pruning limit. **It does not contain L5's fills.**

| test | result |
|---|---|
| **control** — filter for a `transaction_hash` from the dataset's own first rows | **3 rows** |
| **control** — filter for that row's `maker` | **823 rows** |
| dataset `partial` flag | **false** (index covers all 575,630,861 rows) |
| **6 L5 transaction hashes**, spread 2026-04-29 → 2026-07-27, all inside the archive's 2026-04-28 → 2026-08-03 coverage | **0 rows each** |

The filter works, the index is complete, and L5 is absent from it. The `v1`
archive could not be tested at all — its index returned *"loading"* or
*"corrupted and being rebuilt"* on every attempt (0 of 12 tests completed).

**The cause is NOT established** and is not guessed here. The exchange contract
is not an obvious explanation: our own hand-decode of an L5 transaction shows
the same exchange address (`0xe111180000d2663c0091e4f400237545b87b996b`) that
appears in the archive's own sample row. Candidate explanations — a different
exchange deployment for these markets, a collector filter, or a coverage gap —
are untested.

**Consequence: the RPC-pruning limit is not removed by this archive.** Anything
that depended on it needs another route.

## 2. The pruning cuts the other way for recent blocks — and the unit changes

Receipt availability on L5's **recent** transactions: **1.0000** (260 of 260,
then re-run larger). The historical 50% miss really was pruning.

On 2,000 recent public fills:

| unit | count | reduction |
|---|---|---|
| **A** individual public fill | 2,000 | what the 693k-fill history counts |
| **B** same (market, timestamp, outcome, price) cluster | 1,694 | **−15.3%** |
| **C** chain-proven parent order | **845** (over the **1,215** fills that have a proven parent) | **−30.5%** |

**B is a heuristic; C is evidence.** Two fills at the same instant, side and
price *might* be one order swept, or two orders that coincided. Only the chain
says which, and this module never reports B as if it were C.

### Expanded to 1,200 transactions — 0 receipts missing

Of **230** multi-fill clusters, **137 resolved** on chain:

| | |
|---|---|
| SAME_PARENT | **127** |
| MULTIPLE_PARENTS | 10 |
| UNRESOLVABLE | 93 (transactions outside the fetch cap, not pruning) |
| **share of resolved clusters that are ONE parent** | **0.927** |
| **share of their FILLS collapsing into one parent** | **0.9255** |

On the frozen historical sample the same figures were 0.60 / 0.5806 at n=15,
where the misses *were* pruning. The recent sample is **nine times larger** and
is a **different population** (recent activity, not the 2026-03…08 BTC15m
tape). The two are reported side by side and never pooled.

**Parent orders execute in pieces, within seconds:**

* executions per parent: mean 1.473, p50 1, **p90 3, max 9**; **29.0%** of
  parents have more than one execution
* **parent lifetime lower bound** (first → last observed execution, for the
  **11.0%** that span more than one timestamp): **p50 2 s, p90 6 s, max 28 s**
* this is a **lower bound only** — the order existed before its first fill by an
  unknown amount and may have persisted after its last

**Size structure is round at the parent level and fragmented at the fill
level.** Parent quantities: **100.0 (×152)**, then 5.0, 200.0, 10.0, 80.0,
20.0, 25.0, 30.0, 35.0, 95.0. Fill sizes include 6.0, 15.0 and other
non-round values. The odd sizes are *fragments of round orders* — exactly the
signature of partial execution, and a direct reason not to model fill size as a
decision.

## 3. Does L5's inventory recycle? Prospectively — yes, and Gemini's does not

Identical preregistered instrumentation, marks fixed before any data existed.

**P(evidenced complement by mark):**

| mark | **L5 native Polymarket** | **Gemini BTC05M surface shadow** |
|---|---|---|
| 5 s | 0.00 (n=6) | 0.00 (n=17) |
| 15 s | 0.00 (n=6) | 0.00 (n=17) |
| 30 s | 0.00 (n=6) | 0.00 (n=17) |
| **60 s** | **0.40** (n=5) | **0.00** (n=10) |
| **120 s** | **0.60** (n=5) | **0.00** (n=10) |
| **300 s** | **1.00** (n=4) | — |

**Every L5 residual episode observed prospectively recycled by 300 seconds.**
On Gemini, seventeen evidenced fills produced **zero** complements at any mark.

This is the §20 discrimination, measured rather than assumed: L5's inventory
**does** recycle, on a **60–300 second** clock — it is not open-ended
directional tolerance. And the venue we were about to test on does not recycle
at all, which is the same conclusion `L5_HABITAT_SCORE` reached from a
completely different measurement (harder-side continuation 0.0000 at every
horizon).

### Two caveats that must travel with these numbers

**The n is tiny.** Six L5 episodes, four reaching 300 s; seventeen Gemini
episodes. This is a direction, not a rate.

**The early marks are not trustworthy on the Polymarket feed.** L5's public
activity feed lags his fills by a **median of 22.2 s** (p90 48.9 s, max
248.6 s, n=261). An episode is usually already ~22 s old when we first see it,
so a complement that occurred a few seconds after the first fill may not have
been published yet when the 5/15/30 s marks fire. **The 0.00 at 5–30 s is
partly an artifact of publication lag, not necessarily a property of the
market.** The 60/120/300 s marks are progressively more reliable. The Gemini
shadow does not have this problem — it observes its own quotes directly.

### How this sits with the frozen lineage

`MATCHED_SHARE_LINEAGE_V1` found that complements arriving after 120 s were
economically poor (−192 bp/$ OOS) and after 300 s much worse (−475 bp/$). That
is not in tension with recycling at 60–300 s: **the complement arrives, and at
that latency the pair it forms is a bad one.** The two together say the residual
leg is where the money goes, which is what the frozen totals already showed —
with the accounting caveat from §2c that the matched/residual split is
method-dependent and only the total is cash.

## Status

Findings only. Frozen studies untouched, no gate created, no live authorization,
R1 not restarted. All four data processes ran throughout.

## 4. What the behavioral model looks like after collapsing (unit A vs unit B)

The frozen BTC15m tape **cannot** be collapsed to unit C — its receipts are
pruned. It *can* be collapsed to unit B, and the chain gives us that proxy's
accuracy: **96% of resolved multi-fill clusters were a single parent order.**
So this is *"as if same-instant clusters were parent orders"*, right about 96%
of the time on a different population — **not parent-order truth.**

**693,814 fills → 573,584 clusters: 17.3% of apparent actions disappear.**

| metric | **A** public fills | **B** collapsed clusters |
|---|---|---|
| actions per window | 126.13 | **104.29** |
| switches per window | 33.68 | **33.23** |
| run length mean / max | 3.636 / 78 | 3.046 / 71 |
| share of runs = 1 | 0.3321 | 0.3723 |
| **continuation hazard, run 1 → 10** | **0.6745 → 0.8240** | **0.6349 → 0.7792** |
| **hazard rise** | **+0.1495** | **+0.1443** |
| OOS log-loss, previous side only | 0.595008 | 0.637553 |
| OOS log-loss, + state | 0.594049 | 0.635966 |
| state gain (nats) | 0.000959 | 0.001587 |

**Three things follow.**

**The rising-hazard result is not an artifact of partial fills.** It was the
strongest evidence for flow persistence in `L5_CAUSAL_MECHANISM_V1`, and the
obvious worry was that it merely counted one order filling repeatedly. It does
not: the hazard still rises monotonically on the collapsed unit and the *size*
of the rise is nearly unchanged (+0.1443 vs +0.1495).

**The oscillation is real, not fragmentation.** Switches per window barely
move — 33.68 → 33.23. Collapsing removes fills, not direction changes.

**Part of the apparent predictability of the next side was trivial.** Log-loss
gets *worse* on the collapsed unit (0.5950 → 0.6376): at fill level, some of
what looked like "he continues the same side" was just *the same order still
filling*. Remove that and the sequence is genuinely harder to predict — which
is the right direction for an honest model. The state overlay stays small on
both units (0.001–0.002 nats over a previous-side baseline).

**Net answer to "how much of the inferred sequential behavior was exchange
matching":** about **17% of the action count**, and of the *signal*, the part
that was trivially self-predictive — but **not** the run-persistence structure,
which survives collapsing intact.
