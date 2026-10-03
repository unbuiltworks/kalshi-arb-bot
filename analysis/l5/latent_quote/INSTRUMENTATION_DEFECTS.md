# Instrumentation defects — latent-quote shadow

Per §H of the preregistration, the experiment is not altered after seeing early
results **except** for genuine instrumentation defects, documented here with the
before/after and the reason. **No defect below changes a model, a price arm, a
fill rule, a confidence bin or the discovery/validation split.** All were found
in the first 4-minute smoke run, **before any scored result existed.**

## D1 — the public market-wide trade tape lags several minutes

**Found:** 2026-10-02T19:24Z, smoke run, window `btc-updown-15m-1790968500`.

**Symptom.** `flow_n_60 = 0` and `taker_up_share_60 = None` at every decision,
so **S1_FLOW received no input at all** and S5_FULL's flow term was inert.

**Measured.** At a decision instant of 19:24:36Z the newest row from
`GET /trades?market=<conditionId>` was timestamped **19:17:47Z — a 6m49s lag**,
while `GET /activity?user=L5` was returning fills from 19:20:19Z. Ordering was
confirmed newest-first (DESC), so this is latency in the endpoint, not a paging
error.

**Cause.** The public trade tape is an indexed/aggregated view and is not
real-time. It is adequate for **retrospective** fill evidence, and unusable for
a **live** 60-second flow feature.

**Fix.** Two changes, each preserving the preregistered definitions:

1. **Live flow is derived from our own 2-second book deltas**, which are
   unlagged, and is **measured on the UP book only**. Depletion at the Up ask is
   taker BUY pressure; depletion at the Up bid is taker SELL pressure. This is
   observed consumption, not an inferred trade, and is recorded under
   `bookflow_*` as a distinct family from the tape-derived `flow_*` fields — the
   two are never merged into one number.

   *First attempt was wrong and is recorded rather than quietly replaced:* it
   summed depletion across **both** the Up and Down books, which double-counts,
   because Up and Down are complements and a taker buying Up **is** a taker
   selling Down. The symptom was `bookflow_sell == bookflow_buy` exactly
   (647.9 / 647.9) and a degenerate `up_share` of 0.500 at every decision. The
   single-book measurement above replaces it. Caught in the second smoke run,
   still before any scored result.
2. **Defensible-fill evidence remains tape-based and is re-evaluated
   retrospectively** at window close over the full tape, because fill evidence
   is inherently backward-looking. The live loop's fill state is provisional and
   is labelled so; the window-close pass is authoritative.

`trade_tape_lag_s` is now recorded at every decision so the lag is measured
rather than assumed, and any result conditioned on tape freshness can be
filtered after the fact.

**S1_FLOW's definition is unchanged** — "the side opposite recent taker
pressure". Only the *source* of the taker-pressure measurement changes, from a
lagged tape to unlagged book deltas. The model's semantics and its frozen
identity are preserved.

## D2 — shadow orders accumulated instead of repricing explicitly

**Found:** same smoke run.

**Symptom.** 37 live shadow orders after 12 decisions. Each decision placed a
fresh arm set without retiring the previous one, so the same (model, arm, side)
held many resting orders at different prices simultaneously — which would
inflate both the placement count and the fill count.

**Cause.** The runner implemented placement but never invoked the
`cancel_for_reprice` path that `lifecycle.py` already provided.

**Fix.** One resting order per `(model, arm, side)` key. When the policy's price
for that key changes, the existing order is explicitly `CANCELLED` and a new
`PLACED_SHADOW` is emitted; both events are preserved in the order's event list.
When the price is unchanged the existing order simply continues resting and is
**not** re-placed, so placement counts are not inflated.

This implements the preregistered rule — "no silent repricing" — which the
runner had not yet honoured. The rule itself is unchanged.

## D3 — placement accounting must distinguish orders from decisions

**Found:** consequence of D2.

**Fix.** Each record now carries `n_decisions`, `n_placements_new`,
`n_repriced` and `n_live`, and the scored outputs will report **placement count
and independent window/day count separately**, as §H requires. The
~2,000-placement target counts *new* placements, never re-places.

## D4 — P5_STATE proposed a crossing price and was refused 100% of the time

**Found:** 2026-10-02T19:35Z, third smoke run, still before any scored result.

**Symptom.** `P5_STATE` returned `REFUSED_CROSS` on **18 of 18** decisions, so
the arm tested nothing. Cause: it computed `bid + 1 tick` when the complement
was expensive, and the prevailing spread in this market is **one tick**, so that
price lands exactly on the ask.

**Fix.** `P5_STATE` is clamped to `min(bid + step, ask − 1 tick)`, keeping it
strictly passive. The arm's **definition is unchanged** — still "the H1/H3
state-driven price" — and the preregistered rule it now honours is the spec's
own "**Never cross**". An arm permitted to propose a marketable price and be
refused every time measures nothing, which was an implementation defect, not a
property of the policy.

### Not a defect, recorded so it is not later mistaken for one

`P3_IMPROVE_BID` was also refused 18/18. That is **correct and specified**: the
preregistration defines P3 as "improve the bid by one tick, **only if still
passive**", and in a one-tick spread no improving price is passive. P3 is left
exactly as written. Its refusal rate in tight spreads is a **finding about the
venue**, and the frequency with which P3 is testable at all should be reported
alongside its results.

## D5 — cancellations outnumbered placements 2:1, and every record re-serialized the whole order book of ghosts

**Found:** 2026-10-03T14:32Z, from the first deployed run's own heartbeat
counters, **before any scored result**. The deployed run is therefore
`ENGINEERING_ONLY` like the local ones.

**Symptom**, read off the live heartbeat:

```
decisions 444 · placements_new 8,465 · cancellations 16,175
ghosts_created 16,175 · live_orders 66 · ghosts_observing 4,564
sink_dropped 290
```

**16,175 cancellations against 8,465 placements is structurally impossible** if
each cancellation belongs to one placement, and 290 dropped writes means the
tape was already losing records.

**Three causes, all confirmed in the code rather than guessed:**

1. **`cancel()` had no state guard**, so an order already `REPRICED` could be
   cancelled again, each time minting another ghost.
2. **Terminal orders were never evicted from `live`**, so the window-rollover
   loop cancelled every order the window had ever held, not just the resting
   ones — which is where the 2× inflation came from.
3. **Each decision record embedded `list(live.values())` and the entire ghost
   list.** With thousands of ghosts observing, single records reached megabytes,
   which both blew the sink's size cap (the 290 drops) and made the log stream
   unusable.

**Fix.** Three changes, none of which touches a model, arm, fill rule, bin or
the split:

1. `cancel()` is **idempotent**: a non-resting order returns `(o, None)` and
   mints no ghost. Regression-tested against the exact observed numbers.
2. Terminal orders are **evicted** from `live` and emitted exactly once.
3. The output is now a true **append-only event log**, which is what the
   deployment spec asked for and what I had conflated with a snapshot: a
   `decision` row carries state, predictions, quotes and counts **only**, and
   every order transition is its own `order_event` row. Ghost observation is
   bounded at 300 concurrent, and evictions are **emitted** as
   `GHOST_EVICTED_CAP` rather than silently dropped.

**Note on the R4 control arm:** R4_REPRICE_5S is *designed* to cancel and
re-place every decision, so a large ghost population is expected from it and is
not itself a defect — it is precisely the queue-position cost the ghosts exist
to measure. Only the double-counting, the non-eviction and the record bloat were
defects.

## D6 — the ghost cap silently truncated the preregistered 300 s ghost observation

**Found:** 2026-10-03T14:38Z from the second deployment's own event stream,
**before any scored result.**

**Symptom.** `GHOST_EVICTED_CAP` rows were already streaming within the first
window. With 2 execution models × 5 price arms × 6 lifetime arms = 60 keys, the
R4 control arm alone mints ~10 ghosts per 5-second decision and R3_FIXED_8 adds
more, so the cap of 300 was reached in roughly 20 decisions — about **100 s**.

**Why that is a defect and not just a resource bound.** The preregistration sets
`GHOST_OBSERVE_S = 300`, and the ghosts exist specifically to answer whether a
cancelled quote *would later* have filled. A cap that evicts at ~100 s means the
collected data **does not implement the frozen specification**: every ghost
answer is truncated at a third of its intended horizon, which biases the
measured cost of forfeiting queue position *downward* (late fills are never
seen).

**Fix.** `GHOST_MAX_OBSERVING` raised from 300 to **2,500**, sized so the frozen
300 s horizon is actually reachable: ~16 ghosts per decision at a 5 s cadence is
~960 ghosts over 300 s, so 2,500 leaves headroom. Evictions remain **emitted**
as `GHOST_EVICTED_CAP`, never silent, so any residual truncation stays visible
in the tape rather than being assumed away.

**Nothing else changed:** no model, no price arm, no lifetime arm, no fill rule,
no confidence bin, no discovery/validation split, and the preregistration hash
is unchanged at `82d0155e…`.
