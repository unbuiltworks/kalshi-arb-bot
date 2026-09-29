# R6 q10 manifest — PREPARED, NOT ARMED

No authorization is requested by this file. It states exactly what would run.

## Purpose (five measurements in one window)

1. frozen LOCK5 with **real full-depth** input for the first time;
2. the **first private-WS lifecycle** ever observed on this account;
3. **WS vs authoritative REST** agreement, per order, per field;
4. realtime **frozen-vs-L5-mode** behavioural disagreement;
5. **decision freshness** (book age at decision and at POST).

## Frozen parameters — UNCHANGED

θ 0.96 · variant B · hold_s 60 · residual_cap_k 2 · PROFIT_LOCK $5 ·
floor guard OFF · complement bias OFF · clip 10 · THETA_TAKER 0.0695 ·
shared REST limiter **1.5 rps, unchanged**.

## Envelope — identical to R5

| bound | value |
|---|---|
| clip | 10 |
| markets | 1 total / 1 concurrent |
| per-market spend | $81.00 |
| total spend | $81.00 |
| max abs residual | $20.00 |
| daily realized-loss halt | $20.00 |
| max live orders | 8 |
| restartPolicyType | NEVER |
| authorization | fresh, single-use |
| window | venue-confirmed, NOT-yet-started, via discovery |
| `L5_LIVE_PROBE_MARKET` | empty |

## Exact changes since R5

### P0 — real depth into the frozen walk (BEHAVIOUR-AFFECTING)

`l5_live_book_feed.venue_ladder` replaces `touch_ladder` as the engine's
ladder source. `SUBSCRIPTION_TYPE_MARKET_DATA` carries **24–29 levels per
side**; `apply_events` collapsed that to one via `_level()`, so the frozen
theta walk — built to walk depth — had never been given any.

* Every level is a px/qty pair the venue sent. **Nothing is synthesized.**
* Unparseable levels, zero size, and px outside (0,1) are DROPPED.
* Depth from another market is never adopted.
* No venue depth observed → ladder is `None` → the engine's existing
  `if self.ladder else []` guard decides no completion. That is the
  behaviour that already obtained.
* `ladder_health()` reports `source`, level counts and `synthesized: False`.

**This is the one change that can alter what the engine does.** It cannot
admit a price the frozen criterion would refuse — the inequality is
unchanged and applied per level — it can only let a completion fill more of
`want` at prices already admitted. 13 regression tests.

### P1 — private WS lifecycle, OBSERVER ONLY (no behaviour change)

`src/pmus_ws_lifecycle_observer.py`. Subscribes ORDER_SNAPSHOT → ORDER →
POSITION on `/v1/ws/private`. **REST remains authoritative.** The module
imports no money path, no mandate and no engine (asserted by AST test) and
contains nothing that can submit or book.

Records per order and reports: WS→REST latency (median/p95/max), and
agreement on **state, cumQuantity, avgPx, commission** — each as
agree/disagree/not-comparable, with `None` on either side counted as *not
comparable* rather than a mismatch. Also snapshots, reconnects, duplicates,
out-of-order (cumulative quantity going backwards is flagged, never
applied), ws-only and rest-only orders.

**Never infers a fill from disappearance**: an order absent from a snapshot
is recorded `VANISHED_FROM_SNAPSHOT` and is explicitly not a fill, not a
cancel, not terminal. 17 tests.

### P2 — decision shadow, SUBMITS NOTHING (no behaviour change)

`src/l5_decision_shadow.py`. For every frozen decision it logs frozen's
sides, the mode-switch candidate's side, the mode, residual, matched, book
and `submitted: False`. Frozen quotes both sides, so a "disagreement" means
the candidate would have narrowed to one side frozen also offered.
`L5_SHADOW_MODE_SWITCH` stays OFF; two structural tests assert neither the
candidate nor the shadow is referenced by any production module. 8 tests.

### P3 — trade-tape maker persistence (read-only, out-of-band)

`scripts/pmus_maker_persistence_probe.py`, type 3 with `maker.side` /
`taker.side` and microsecond stamps. Classifies maker-side events into
single-price vs multi-price-at-one-instant, and consecutive groups into
replenish vs reprice. **Market-wide, not attributed to L5** — evidence about
BTC-15m maker behaviour, not proof about L5. Not part of the probe process.

### Carried forward, unchanged

`6685e60f` terminal-verdict cache (reads/tick 2.376 → 1.0) · `440c9696`
completion-ladder seam fix and bounded cancel recovery · the canonical
accounting feed · all lifecycle safety invariants.

## What R6 will report

| metric | source |
|---|---|
| book age at decision, at POST (median/p95/max) | `timing_report()` |
| REST calls/tick, cache hits | `status_reads`, `status_cache_hits` |
| ladder provenance and depth used | `ladder_health()` |
| completions admitted, and at what depth | native tick journal |
| WS event → observed latency | observer |
| WS → REST confirmation latency | observer |
| WS/REST state, cumQuantity, avgPx, commission agreement | observer |
| snapshot/reconnect behaviour, duplicates, missing, out-of-order | observer |
| frozen vs mode-switch action per decision | decision shadow |
| full canonical accounting + settlement | canonical feed |

## Known and unresolved — read before authorizing

* **Decision freshness will still miss the <1s target.** Measured p95 is
  **1140 ms** at 1 read/tick against rps 1.5, because 2 tokens/tick at
  1.5 rps is a 1.333 s floor. Passing needs a higher rps (not changed) or
  the WS lifecycle cutover (not done). Expect PDI rejections in a trending
  window; they are the measurement, not a failure.
* **No private-WS lifecycle event has ever been observed.** The schema in
  P1 is tolerant by design precisely because it is unknown. R6 is the first
  opportunity to see one, which is why the observer holds no authority.
* **Real depth is new in live.** It has passed regression but has never run
  against a live book. It can only increase completion size at admitted
  prices, and the residual cap and mandate still bound everything.
* The 20 pre-existing suite failures are unchanged and unrelated
  (orderbook 5, spread_scanner 9, and 6 others).

## Test evidence

P0 13 · P1 17 · P2 8 · plus existing R5/R4/freshness suites. Full suite
**10,069 passed, 20 failed** — the pre-existing 20. Five gemini tests failed
once on a transient GitHub 503 and pass on re-run.

## NOT in this manifest

No AWS migration. No Odds API. No production WS lifecycle cutover. No R6
arming. No rps change. No strategy, threshold or risk-bound change.
