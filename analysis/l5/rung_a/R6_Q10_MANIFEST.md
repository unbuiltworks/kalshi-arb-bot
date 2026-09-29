# R6 q10 manifest — PREPARED, NOT ARMED

**R6 candidate SHA: see `R6_CANDIDATE_SHA` below. Frozen.**

## R6 wiring change (authorized, wiring only)

P1 and P2 were built but referenced by NO production module, so at the
previous SHA R6 could not have delivered purposes 2, 3 or 4. Wired now, and
only this:

| site | what it does |
|---|---|
| `pmus_native_money_path.__init__` | `ws_observer = None`, `decision_shadow = None` |
| `pmus_native_money_path._status_once` | hands the AUTHORITATIVE REST verdict to `on_rest` AFTER REST has spoken; the verdict is not rebound |
| `pmus_native_money_path.on_book_state` | calls `decision_shadow.compare` AFTER `eng.on_book` returned its intents |
| `pmus_native_money_path.resolve_open_orders` | calls `decision_shadow.observe_fill` where a fill books |
| `scripts/l5_pmus_live_oneshot.native_work_window` | constructs, attaches, starts the private stream, journals, stops |

Every call is wrapped so an observation failure cannot raise into the
trading path. Structural tests now assert the permitted call sites by name
and prove line-by-line that none of them reaches submission, cancellation,
canonical booking, inventory mutation, risk mutation or an execution
decision; that the shadow's return value is never assigned or returned; and
that the shadow is called strictly after `eng.on_book`.

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

## Decision freshness — audited, improved, and honestly short of p95

Correction to the earlier model: `submit_body` does **two** writes
(`POST /v1/order/preview` then `POST /v1/orders`), so a submitting tick
costs **1 read + 2 writes = 3 tokens**, not 2.

Critical path, fresh book -> POST, and what was done:

| # | call | required synchronously? | action |
|---|---|---|---|
| 1 | `GET /v1/order/{id}` per nonterminal order | YES — the LOCK5 invariant | **gated on the trade tape** |
| 2 | `GET /v1/order/{id}` in `_ensure_side_clear` | YES — ordered replacement | cached since `6685e60f` |
| 3 | `POST /v1/order/{id}/cancel` | YES — no two live quotes per side | unchanged |
| 4 | `POST /v1/order/preview` | YES — echo validation before money moves | unchanged |
| 5 | `POST /v1/orders` | YES | unchanged |

Only the redundant read was removed. A fill of our order is a trade in this
market, and the trade tape is already subscribed and folded; when it has
printed nothing since the last sweep, no fill can have occurred. Forced
sweep on UNKNOWN, tape movement, first sweep, no tape (fail closed), or age
>= 5.0 s. The one way a skip can be wrong — a reject/expiry prints no trade
— leaves the order LIVE with its money charged and its side blocked, which
is strictly less exposure than the truth.

Measured on the 468-tick R5 workload, rps unchanged at 1.5:

| variant | median | p95 |
|---|---|---|
| R5 actual, 2.38 reads + 2 writes | 1740 ms | 2440 ms |
| `6685e60f` cache, 1.00 read | 913 ms | 1140 ms |
| **+ sweep gate, ~0.30 read** | **413 ms** | **1100 ms** |
| + sweep gate, ~0.15 read | 313 ms | 1047 ms |

**Median target MET (413 ms < 500 ms). p95 target NOT met, and it cannot be
met by removing reads.** Isolating the bound:

| | median | p95 |
|---|---|---|
| 0.00 reads + 2 writes | 260 ms | **480 ms** PASS |
| 0.30 reads + 1 write | 407 ms | 1100 ms FAIL |

p95 is bounded **entirely by the blocking read**. Writes drain the bucket
but never sleep; a read landing on an empty bucket waits 667 ms, and the
p95 tick is precisely the tick where the tape moved and a read is therefore
REQUIRED. Reaching p95 <1 s needs a higher rps or the WS lifecycle cutover.
Both are out of scope, so this is reported rather than worked around.
**Expect PDI rejections in a trending window; they are the measurement.**

## Known and unresolved — read before authorizing

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
