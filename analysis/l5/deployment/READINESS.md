# L5 production readiness — Polymarket vs Gemini

States: **NOT READY** · **SHADOW** (modelled, no venue evidence) · **VERIFIED**
(venue or test evidence) · **LIVE** (running in production)

| Component | Polymarket | Gemini |
|---|---|---|
| L5 observation | **VERIFIED** | **NOT READY** |
| L5 prediction | **SHADOW** | **SHADOW** |
| market mapping | **VERIFIED** | **NOT READY** |
| executable shadow | **SHADOW** | **NOT READY** |
| fill evidence | **NOT READY** | **NOT READY** |
| accounting | **SHADOW** | **NOT READY** |
| risk | **VERIFIED** | **NOT READY** |
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
