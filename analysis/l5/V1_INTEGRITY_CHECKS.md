# Three integrity checks before any economics

**Checks 2 and 3 are RESOLVED. Check 1 is NOT, and it blocks the experiment.**
No economic result is produced here.

---

## 1. Raw depth schema — **FAILS on the harvest channel**

**Full L2 exists, is received, and is persisted — but it is not in what I can reach.**

`_walk_market_events` (`src/pmus_ws_evidence_recorder.py:199-207`) captures the
WS message's full arrays: `"bids": msg.get("bids"), "asks": asks`. The whole raw
message is appended verbatim to `/state/pmus-ws/raw/…jsonl` on the volume.

But `_level()` (`:259-265`) returns `levels[0]` — **top of book only** — and that
is what lands in per-market state, and therefore everything `PMUS_BOOK_ROWS`
emits to stdout, and therefore everything the harvest can capture.

So the answer to your question is: **the harvested gz slices carry the capped
top-of-book telemetry representation, not the full-depth messages.** The full L2
is on the volume, unreachable from here without touching the live collector.

### Measured on one complete window (`0245z`), using the only local source that carries ladders

| | |
|---|---|
| raw tape rows | 82 |
| depth-bearing rows | 25 |
| levels per side — bids | **median 19, max 31** |
| levels per side — offers | **median 31, max 38** |

The venue publishes a deep ladder: ~19–31 price levels a side, not a touch.

### Exact-level queue reconstruction, over 4,012 hypothetical resting prices

| source | reconstructable |
|---|---|
| **full L2 ladder** | **12.4%** |
| **top-of-book only** | **1.2%** |

**Full L2 reconstructs 10.3× more resting prices.** At 1.2% the simulator would
refuse to rest almost everywhere, and every result would describe the feed rather
than the market. That is exactly the top-of-book limitation you said not to
accept, and I am not accepting it.

Worth noting even the 12.4% is low: a basis-derived complement often sits outside
the displayed ladder entirely. That is a real finding about where our prices fall,
and it only becomes measurable with full depth.

### What reaching the full ladder costs

Every route needs a change to a live collector, which collides with the unresolved
packaging gate:

1. widen `_level()` / the emitter to carry N ladder levels in `PMUS_BOOK_ROWS`;
2. add a read-only HTTP route serving raw tape parts from the volume;
3. raise the cadence of `pmus-btc15m-tape`, which already emits **full ladders** but only ~25 depth rows per window.

All three restart a healthy collector that carries **no commit pin** and tracks
`feature/l5-transfer-backtest`, last deployed 2026-09-26 on `4b5b0e84`. Option 1
is the smallest diff and the only one that improves the stream we already trust.

---

## 2. De-duplication — **RESOLVED, and it surfaced an upstream defect**

**My defect, fixed.** The loader de-duplicated individual *trades* on
`(t, px, qty, trade_time)` and book states on `t`. A venue can legitimately print
twice for the same size at the same price in the same instant, and collapsing
those destroys real volume — the error runs in the dangerous direction, because
volume is what fills us.

De-duplication now happens at the **log-line** level only — `(timestamp, message)`,
the genuine duplicate when overlapping harvest rounds capture the same Railway
line. Every row inside a document is kept. Source order is preserved by carrying
`(file index, line index, array index)` and sorting **stably**, so equal
timestamps keep the order the venue sent.

Mutation-tested, as asked: two identical prints in one document **stay two**; two
book events sharing `t` **stay two, in source order**; a repeated log line is
still collapsed.

**Upstream defect I cannot repair and will not hide.** The recorder itself
de-duplicates before emitting: `_trade_row_key` = `(slug, t, px, qty, trade_time,
dir)` in `emit_new_trades` (`:1107-1133`). Two genuinely distinct prints identical
in all six fields are **dropped at the source** and reach no consumer. Nothing
downstream can recover them. Frequency unknown; the effect is to **understate**
traded volume, so fill estimates built on this stream are conservative on that
axis. There is no venue trade ID or sequence number in the emitted schema to
disambiguate with — another reason the raw message matters.

---

## 3. Same-print queue exhaustion — **RESOLVED (behaviour was right, wording was wrong)**

The implementation already did what you describe: a single print's quantity
consumes `queue_ahead` first, and the remainder of **that same print** fills us.
No separate later print is required. The docstring said otherwise and is corrected.

Pinned by tests:

- 100-deep queue, one 107-lot print → **fills 7 from that same print**
- 500-deep queue, 495-lot print → **fills nothing**, queue left at 5
- then a 20-lot print → consumes 5, fills 10 (capped by clip)
- a print away from our price → never reaches us
- 50 book states with deep size and no prints → **zero fills**
- a requote → **back of the new level's queue**

One behaviour the tests made explicit: after a partial fill, the remainder of our
own order is cancelled, because those contracts made that side the long side and
buying more is an EXTEND. That is intended continuous-inventory behaviour — a
partial fill re-prices the whole book rather than leaving a stale order deepening
the imbalance.

---

## Status

Checks 2 and 3 resolved and tested (11 tests). **Check 1 blocks**, and it needs a
decision about touching the live collector before the event-stream experiment can
produce anything worth reading.
