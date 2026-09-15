# L5 production readiness — Polymarket vs Gemini

States: **NOT READY** · **SHADOW** (modelled, no venue evidence) · **VERIFIED**
(venue or test evidence) · **LIVE** (running in production)

| Component | Polymarket | Gemini |
|---|---|---|
| L5 observation | **VERIFIED** | **NOT READY** |
| L5 prediction | **SHADOW** | **SHADOW** |
| market mapping | **VERIFIED** | **VERIFIED** |
| executable shadow | **SHADOW** | **SHADOW** |
| fill evidence | **NOT READY** | **NOT READY** |
| accounting | **SHADOW** | **NOT READY** |
| risk | **VERIFIED** | **SHADOW** |
| bounded live readiness | **NOT READY** | **NOT READY** |
| realized P&L | **LIVE (negative)** | **NOT READY** |

---

## Evidence, cell by cell

### L5 observation — Polymarket VERIFIED / Gemini NOT READY

Polymarket: the 5m/15m recorder runs 24/7 (coverage 0.9892 / 0.9952, lead
119.1 s / 119.8 s, 0 feed failures, 0 slug collisions). Observation latency is
measured, not assumed: **p50 15.0 s, p95 29.8 s, n=208**, a ~31 s batch
refresh. The chain route reaches **~3.8 s** and beat the feed on 10 of 10
joined observations.

Gemini: **L5 has no Gemini identity.** There is nothing of L5's to observe
there, and that is a property of the world, not a gap we can close. What Gemini
offers is *market* observation, scored under market mapping.

### L5 prediction — SHADOW on both, because it is venue-neutral

`v0.2-85b4260cc30a`, frozen and accumulating: **60 frozen / 45 resolved,
0 tampered** at the last digest, with the v0.1 lineage preserved
(seal `bcd099ae9f7a5b4a`, identical across two boots). Early ACT rates by
preregistered horizon: 1 s 0/50, 5 s 6/50, 15 s 26/50, 30 s 42/50.

**Still EARLY. Not tuned against its own prospective set.** The same intent
stream feeds both adapters, which is why this cell cannot differ by venue.

### market mapping — Polymarket VERIFIED / Gemini NOT READY

Polymarket: window slugs parse deterministically
(`btc-updown-{15m|5m}-{epoch}`), and `conditionId` / `clobTokenIds` resolve
through gamma. Exercised live.

Gemini: **no mapping exists.** Nothing maps a Polymarket BTC 15 m window onto a
Gemini contract, and none was built here. Until one is, "the same L5 intent on
Gemini" has no referent. This is the first Gemini blocker.

### executable shadow — Polymarket SHADOW / Gemini NOT READY

One evaluator (`src/l5_adapters/evaluate.py`) serves both venues, so a
difference between them is economics rather than an implementation accident.
For each frozen intent it records available price/depth, spread, book age,
expected fee **and its basis**, submission price, effective cost, ceiling test,
`would_submit`, `would_fill`. Verified on a real book: Up prices off the YES ask
(0.57), Down off the NO ask (0.45).

Gemini is blocked on one specific fact: **`GeminiAdapter.taker_fee` returns
`None` — "not vendored: GAP (unverified)"**. The evaluator therefore refuses to
price Gemini conservatively rather than substituting a zero. That refusal is
correct: a fabricated 0 fee reads as the cheapest possible execution and would
make Gemini win every routing decision on economics we have never confirmed.
**Source-confirming the Gemini taker fee is the second blocker**, and the
maker-basis path already works when asked for explicitly.

### fill evidence — NOT READY on both

No adapter evaluation has ever reached `filled`, on either venue. The type
makes that reachable **only** from venue evidence: `filled=True` without
`fill_evidence` raises, and `liquidity_role` cannot be set at all without it.
Polymarket has historical executor fills, but those are not adapter evaluations
and are not counted here.

### accounting — Polymarket SHADOW / Gemini NOT READY

The invariant is fixed and deployed (`54d1c75d`): a market closes only on a
venue-confirmed `realized` row, so an unconfirmed settlement can no longer drop
out of `combined_loss` and out of settlement polling simultaneously. Pinned by
7 tests.

**Not yet VERIFIED, and this is the honest reason:** the executor is halted by a
pre-existing `PMUS_SPRING_SHUTDOWN` latch that the cutover restart fired, which
short-circuits before `boot_assertions`. So the corrected `combined_loss`, the
four markets returning to the charged set, and the risk-reduction-only lane are
all **unobserved in production**. Reconciliation stands at executor −23.2786 vs
conservative **−31.0612**.

### risk — Polymarket VERIFIED / Gemini NOT READY

−$30.00 daily stop unchanged; `combined_loss` = realized + negative unsettled
floors; risk-reduction-only lane; deadman/shutdown latch that holds the
container up rather than exiting (so a halt is never read as a crash and rolled
back); settlement polling continues while halted. Exercised live at the cutover:
`SHUTDOWN_VERIFIED`, 0 orders cancelled, held inventory preserved.

Gemini has **no risk shell at all** — no stop, no inventory bound, no deadman.
Third blocker, and the one that must not be skipped if capital ever moves there.

### bounded live readiness — NOT READY on both

Polymarket: the conservative stop is **already breached** (−31.06 vs −30.00), so
no new campaign may start by the standing rule. Gemini: blocked on all three
items above.

### realized P&L — Polymarket LIVE (negative) / Gemini NOT READY

Polymarket realized P&L is genuinely flowing and is **negative**: 32
venue-confirmed markets, **−$47.7316**, with a further 8 markets (30.3% of
traded quantity) in a terminal `SETTLED_REALIZED_UNCONFIRMED` state and excluded.
Confirmed-only economics are deteriorating: mean per market −0.70 → −2.28 across
chronological halves, winners 4/16 → 1/16.

Gemini has never traded; realized P&L is zero, not unknown.

---

## Ordered path to money, and what each step actually unblocks

1. **Clear the shutdown latch** → the accounting fix's behaviour becomes
   observable and the Polymarket accounting cell can move SHADOW → VERIFIED.
   Nothing downstream can be trusted until the stop is reconciled.
2. **Run the Polymarket executable shadow against live books** on the v0.2
   intent stream → collects `would_submit` / `would_fill` at scale.
3. **Source-confirm the Gemini taker fee** → unblocks the Gemini evaluator.
4. **Build and test Polymarket↔Gemini market mapping** → gives Gemini intents a
   referent.
5. **Gemini risk shell** → stop, inventory bound, deadman.
6. **Bounded live probe, one venue, tiny** → first real `filled` evidence.

**The router already refuses to trade when neither venue is eligible**, and that
is not a fallback path: unknown depth fails closed, capacity is a constraint
rather than a tiebreak, and `would_fill` — an upper bound from visible depth —
only ever breaks ties, so it can never outrank a real cost difference.

## The standing question

> Does this move us toward an autonomous L5 engine producing safely realized
> dollars on Polymarket and/or Gemini?

Intent type, adapters, router: yes — they are the execution path. Latency and
chain-observer work: yes, but it is **done**, and further latency research is
now deprioritized. FIDELITY v0.2: yes, and it should **accumulate untouched**
rather than acquire a v0.3.


---

# Update 2026-09-15T12:5xZ — Gemini mapping and fee economics are CLOSED

Two of the three Gemini blockers are gone, and one of them was never a blocker.

## Market mapping — NOT READY → VERIFIED

The mapping is exact arithmetic, not name matching. Both venues key a window on
the same real instant: the Polymarket slug carries the **start**, the Gemini
ticker carries the **expiry**.

```
btc-updown-15m-1789470900  ->  expiry 1789471800  ->  GEMI-BTC15M2609151130-UP
```

**Verified against the venue, not an index.** Measured live:
`/v1/prediction-markets/events` listed BTC05M but **not** BTC15M, while
`GEMI-BTC15M2609151245-UP` returned a live two-sided book (0.62 / 0.70). The
index is incomplete, so existence is established by constructing the symbol and
asking for its book. A one-sided book is *not* a mapping success — a contract
that cannot be priced on the side we want is, for routing, indistinguishable
from absent. A venue that cannot be asked is **unknown, not absent**.

ETH is deliberately unsupported: only BTC is verified, and guessing is worse
than declining because a wrong mapping routes real capital into the wrong
contract.

## Fee economics — NOT READY → VERIFIED, and I had this wrong

I reported the Gemini taker fee as an "unverified GAP". **It was never
unknown — only unvendored.** The official Gemini prediction-market schedule,
**user-verified 2026-08-31**, has been in this repository the whole time, in
`gemini_maker/completion_frontier.py`:

```
maker  0.0175 * C * P * (1-P)      (waived Sep 2 - Dec 31)
taker  0.07   * C * P * (1-P)
crypto maker rebate: 30% of the TAKER formula, maker fills priced 20c-80c,
                     floored per fill to the cent
```

`GeminiAdapter.taker_fee` returned `None` marked *"not vendored: GAP
(unverified)"*, which made every conservative Gemini evaluation refuse for want
of a number we already held. Now vendored next to `maker_fee` so the two cannot
drift.

**The waiver is deliberately NOT applied**, and neither is the rebate. Pricing
against a time-bounded promo overstates the venue the moment it lapses, and a
rebate is credited, not assumed — the same rule already enforced on Polymarket.
The guard that refuses a null fee is retained and separately tested, so closing
this gap did not remove the protection.

## Executable shadow — NOT READY → SHADOW, with a live result

The whole chain ran end to end against live books:

| stage | result |
|---|---|
| intent | `int-b06b16f442b36d7a`, `btc-updown-15m-1789475400`, ceiling 0.75 |
| mapping | **exact** → `GEMI-BTC15M2609151245-UP` |
| price / fee | 0.66 / **0.16** → effective **0.676**, within ceiling ✓ |
| would_submit | **true** |
| would_fill | **false** |
| **route** | **NEITHER** — *"insufficient or unknown capacity for 10 (depth=1.0)"* |

**The binding Gemini constraint is capacity, not fees or mapping.** The book was
~**1 contract deep** at the touch against a wanted 10, and the router declined
rather than routing capital into a book that cannot fill it. Observed spreads
are also wide against Polymarket's typical 1-2c: 0.62/0.70 on the 15m,
0.93/0.99 on the 5m.

This is exactly the evidence the Gemini adapter exists to produce, and it is
**not** an argument for forcing capital there.

## Remaining Gemini blocker

**Risk shell — still NOT READY.** No stop, no inventory bound, no deadman. That
is the last item before any Gemini bounded live probe, and it must not be
skipped.


---

# Update — Gemini risk shell exists (third blocker closed)

**Gemini risk: NOT READY → SHADOW.** A shell now exists; it is not live and is
not an authorisation to trade.

`src/l5_risk/` holds the risk arithmetic **once**, venue-neutral: guaranteed
settlement floor, negative-floors-only aggregation, combined loss, and an
**inclusive** budget boundary. It is arithmetic on contracts and cash — no
price, no book, no mark — which is what lets it sit in a stop without
inferring P&L from a valuation.

## It is provably the same arithmetic the executor uses

`tests/test_l5_risk_shell.py` asserts numerical agreement with the live PMUS
implementation across four inventory shapes, on `combined_loss`, and on the
inclusive boundary. If either moves, the test fails.

**The PMUS executor is deliberately NOT refactored to import this.** Changing a
running money path to share code is a production change with no safety benefit;
the equivalence test gives the same protection without touching it.

Reproduces the documented live case exactly: `min(20.34, 16.06) − 21.5939 =
−5.5339` — a loss knowable before settlement and invisible to a realized-only
stop.

## The shell refuses, and names what it hit

`RiskShell` cannot be constructed without real bounds — a non-negative stop, a
zero inventory cap, a zero spend cap, zero concurrency or a zero deadman all
raise. A shell that works unconfigured is one that can ship with nobody having
decided the limits.

Every refusal names its bound: `DAILY STOP`, `DEADMAN`, `INVENTORY CAP`,
`MARKET SPEND CAP`, `CONCURRENCY`.

The spend bound prices at **the intent's own ceiling**, not the quoted price: a
bound computed from a hoped-for fill is not a bound.

## GEMINI_PROBE_SHELL — written down, not authorised

`stop −5.00 · max 10 shares · max 5.00 per market · 1 concurrent · deadman 300 s`

Deliberately tiny. The measured Gemini book — ~1 contract deep at the touch,
6–8c spreads — is the reason the inventory bound is **small**, not a reason to
widen it. **Gemini is not live and this shell does not make it live.**

## Gemini blockers: all three closed

| blocker | state |
|---|---|
| market mapping | **VERIFIED** — exact expiry arithmetic, venue-confirmed |
| fee economics | **VERIFIED** — vendored from the user-verified schedule |
| risk shell | **SHADOW** — exists, bounded, tested, not live |

What remains before any Gemini capital moves is **fill evidence**, and the live
shadow already says what stands in the way: the router returned **NEITHER** on
a real book for insufficient capacity. Capacity, not fees or mapping, is the
binding constraint.


---

# Update 2026-09-15T13:2xZ — the executable shadow is RUNNING, and the touch was the wrong question

The board's `executable shadow` row moves from a single-snapshot evaluator to a
**continuous dual-venue stream** (`src/l5_shadow/`), and the first hours of it
overturn the conclusion the single Gemini snapshot supported.

## Gemini is not depth-limited. It is price-limited.

The earlier reading — *"depth 1 against a wanted 10, router declined"* — was a
reading of the **touch**. Read in full, the same kind of contract holds real
size behind the touch. Measured live 13:04Z:

> **Correction.** This paragraph originally added *"taken through a collector
> that literally requests `?limit_bids=1&limit_asks=1`"*. That attribution was
> wrong: the path that produced it read a **full, untruncated** book and saw a
> genuine 1-share touch. The `limit=1` truncation is real, hides a median 182
> shares, and lives in two *other* services — see
> `analysis/l5/gemini_depth/INVESTIGATION.md`, which measures both separately.

```
GEMI-BTC15M2609151315-UP   bids 0.72x1, 0.70x500, 0.67x223
                           asks 0.78x500, 0.81x789
```

Across the first 23 complete intents the Gemini completion rate is **flat at
0.263 for every candidate size — q=1, 2, 5, 10 and 20 alike**. A venue that
fails at 10 but succeeds at 1 is size-limited; one whose rate does not move with
size is not. When Gemini can do the trade at all, it can do 20 as easily as 1.
**Its binding constraint is price, not depth.**

## The two venues are complementary by size, not substitutes

Polymarket's crypto markets declare `orderMinSize: 5`. So:

| candidate size | Polymarket completion | Gemini completion |
|---|---|---|
| q=1 | **0.000** (cannot be submitted at all) | 0.263 |
| q=2 | **0.000** (cannot be submitted at all) | 0.263 |
| q=5 | 0.263 | 0.263 |
| q=10 | 0.263 | 0.263 |
| q=20 | 0.263 | 0.263 |

This already changed a routing decision in production data: a q=1 MODEL intent
routed **GEMINI**, not because Gemini was cheaper — it was 2c dearer — but
because Polymarket could not accept the order.

Since L5 appears to accumulate inventory through repeated small fills, a venue
that accepts q=1 and q=2 is not a second-best alternative to one that does not.

## Polymarket is systematically cheaper where both can trade

Cross-venue gap on the wanted side: **p50 0.04, p95 0.06, max 0.09**, and
Polymarket was cheaper in **6 of 6** contested routes. The 0.09 case was a 5m
window with ~77 s left where Polymarket quoted Up at 0.05 and Gemini at 0.14
with **no bid at all** — the mapping verified exactly (both expire
2026-09-15T13:20:00Z), so the divergence is a venue fact. Gemini's 5m book
degrades badly near expiry.

## Fees, from the venue, per window

Polymarket declares `feeSchedule {"rate": 0.07, "takerOnly": true}` and
`makerRebatesFeeShareBps 10000` on every market object: **its maker pays no
fee**. Gemini's maker pays ceil-cent `0.0175*C*P*(1-P)`, and the Sep 2 – Dec 31
waiver is deliberately not applied. At q=1 Gemini's ceil-cent rounding alone
costs ~2c/share on a 66c contract (0.0157 → 0.02); by q=10 the two venues' taker
fees are within 0.0003/share of each other. **Fee asymmetry is a small-size
effect on the taker side and a structural one on the maker side.**

## Patience is measurable, and it pays sometimes

Of intents re-read at +30/+60/+120 s, **5 of 15 later crossed the ceiling on
Polymarket and 3 of 15 on Gemini**. An intent that is not executable now is not
an intent that is never executable, which is the first direct evidence for the
patience the operating model asserts.

## What is still limiting, and it is not the venues

`l5_observation_lag_s`: **BTC15m p50 25.0 s, p95 29.9 s; BTC5m 133 s.** L5's own
price is frequently gone by the time we see the action. Several NEITHER routes
are exactly this: L5 bought at 0.48, and 18 s later both venues were 0.53/0.58.
The binding constraint on realtime replication is the **observation clock**,
not venue depth or venue fees.

## Maker evidence: 0 fills, and that is the honest number

19 submittable hypothetical placements, **10 decided, 0 evidenced fills, 1
evidenced no-fill, 9 undecided for want of any print**. Median queue ahead of us
at the join price: **277 shares**. Nothing here is counted as a fill, and
"nobody traded" is reported as undecided rather than as a no-fill.

## Board changes

| Component | Polymarket | Gemini | change |
|---|---|---|---|
| executable shadow | **VERIFIED** | **VERIFIED** | continuous stream, both cohorts, one evaluator, live books |
| fill evidence | **SHADOW** | **SHADOW** | modelled maker outcomes now exist with an evidence test; still 0 evidenced |
| market mapping | VERIFIED | **VERIFIED** | 5m and 15m both confirmed against the venue clock |

Everything else on the board is unchanged. Promotion gates are now written down
in `PROMOTION_GATES.md` **before** any of them is green, and no venue is
promotable today.

## Corrections to this document

* The `market mapping` body section above still described Gemini as NOT READY
  after the header table had moved it to VERIFIED. The mapping is VERIFIED; the
  stale paragraph predates the 12:5xZ update and is superseded by it.
* The `executable shadow` body section describes `src/l5_adapters/evaluate.py`,
  the single-level evaluator, and its account of the Gemini fee as an
  "unverified GAP" is superseded twice over: the fee was vendored at 12:5xZ, and
  the ladder evaluator `src/l5_shadow/executable.py` now carries the routing
  path.


---

# Update 2026-09-15T14:2xZ — the price gap, measured against L5's own edge

The executable shadow answered "could we match L5's price" with NEITHER most of
the time. True, but not a decision. This is the decision.

## How far above L5's own achieved price the market was, when we could first look

| | Polymarket | Gemini |
|---|---|---|
| already **at or under** L5's price | **45.8 %** | 23.3 % |
| executable at +1c | 57.3 % | 29.4 % |
| executable at +2c | 63.0 % | 33.8 % |
| executable at +5c | 80.6 % | 59.7 % |
| executable at +10c | 92.5 % | 81.6 % |
| median slack when above | 4c | 6c |

n = 227 / 228 evaluations. Measured on the quoted touch, **excluding fees**, so
every figure is a *lower* bound on the concession really required.

## And here is why "we'd only need a cent" is not a small ask

L5's measured trading drag is **0.037 % per dollar**. At the median intent
price of **0.43**:

| concession | cost, as a multiple of L5's ENTIRE per-dollar edge |
|---|---|
| **1 cent** | **62.9×** |
| median slack on Polymarket (4c) | **251×** |
| median slack on Gemini (6c) | **377×** |

One cent is not a rounding error against this strategy — it is the strategy,
sixty-three times over. So the 45.8 % of intents where Polymarket was *already*
at or under L5's price are not a starting point to be widened; **they are the
entire addressable set**, and loosening the ceiling to raise the executable
rate would convert a replication into a different, losing trade.

## What that implies for the engine, concretely

1. **Do not widen the ceiling.** Every documented "executable at +Xc" line
   above is a measurement, never a proposal.
2. **Reducing observation lag is the only lever that grows the addressable
   set** without touching the economics — it moves intents into the 45.8 %
   rather than paying to reach them. This is why the chain route is the
   priority over any venue work. Re-measured over n=73 joined fills (the
   earlier 19.6 s came from 10 observations): the chain is readable **0.37 s**
   after its block while `/activity` publishes **15.90 s** after it, a median
   lead of **15.16 s**, winning **73 of 73**. Against a median intent age at
   decision of 17.6 s, that is roughly **2.4 s** spent instead of 17.6 s.
3. **Polymarket is roughly twice as reachable as Gemini at zero concession**
   (45.8 % vs 23.3 %), which is a sharper venue difference than the depth
   comparison ever was.
