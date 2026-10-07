# Two-token book-state shadow collector — DESIGN

Module: `src/l5_book_shadow.py` · entrypoint: `scripts/l5_book_shadow_run.py` ·
image: `deploy/l5_book_shadow/Dockerfile` · tests: `tests/test_l5_book_shadow.py`

**STATUS: BUILT, NOT DEPLOYED.** Deployment is the parent session's, and an
armed probe is working a market; see `RUNBOOK.md`.

---

## 1. The one artifact this exists to create

> **Both outcome tokens' book state at the moment L5 turns from EXTEND to
> COMPLETE.**

Historical reconstruction cannot supply it. The only Polymarket L2 on disk is
the Zenodo 2026-08-21..24 capture, which overlaps **zero** canonical windows
(`analysis/l5/BOOK_ACQUISITION.md`, `analysis/l5/DECISIVE_VERDICT.md`). The
state therefore has to be captured **forward**, which is all this collector
does.

### The failure it refuses to repeat

Every existing book tape in this repo records **one side and derives the
other**. `src/pmus_btc15m_l5_shadow.py`:

```python
# NO side is the complement of the YES book, as on every binary venue
"no_bid": (1.0 - ask) if ask is not None else None,
```

On a CLOB where the two outcome tokens are quoted **independently**, that
identity is an assumption, and it destroys exactly the question being asked —
whether the **opposite** token's quote crossed a candidate complement price
before or after L5's fill. A derived complement can never cross anything; it
just mirrors the side you already had.

So: **each outcome token's own book, from its own feed, as its own row**, and a
market whose tape shows fewer than two token identities is reported
**DEFECTIVE** (`token_coverage`, `defective_markets`, `verify_both_tokens`)
rather than analysed. The test suite asserts a single-token tape fails.

---

## 2. Reuse decision — what was extended, and why a new module

Inspected first, per CLAUDE.md ("reconcile duplicate implementations"):

| candidate | records both tokens independently? | verdict |
|---|---|---|
| `src/pmus_book_tape.py` | No — one slug, one book; PMUS only | pacing/drain/heartbeat **patterns reused**, module not extended |
| `src/l5_pmus_auth_book.py` | No — BTC15m single-slug PMUS auth book | not applicable (wrong venue for L5) |
| `src/pmus_ws_evidence_recorder.py` | No — per-slug `bid`/`ask`, no second token | outbound-frame guard pattern reused (`guard_outbound`) |
| `src/pmus_flow_collector.py` | No — one `bbo` per slug | first-difference volume idea not needed here |
| habitat streams (`pmus_books`, `pmus_flow`, `intl_books`, `intl_flow`) | No — slug-keyed single books | nothing to extend |
| `src/pmus_sports_live_collector.py` | No — discovery/cap layer over single-book polling | selector idea reused conceptually |
| `src/l5_pmus_realtime_observer.py` | No — L5 activity + one PMUS book | its `l5_activity` data-api shape reused |
| `src/l5_native_shadow.py` | **Partly** — the only code in the repo that fetches **both** `clobTokenIds` books (`/book?token_id=`) — but **bids only**, no tape, no provenance, no pacing, and it is a decision shadow, not a recorder | **its two-token fetch pattern is the thing that was lifted** |

**Decision: new module, built on `l5_native_shadow`'s two-token fetch.** No
existing collector records both tokens as independent rows on an append-only
tape; the closest (`l5_native_shadow`) is a shadow decision loop that keeps
only best bids in memory. Extending a 2,200-line PMUS-single-slug recorder
(`pmus_ws_evidence_recorder`) would have meant changing the recorder that is
currently producing live evidence — the opposite of safe. The new module is
~700 lines, has one job, and its image contains nothing else.

---

## 3. What it records

Three venue-side feeds plus two process feeds, all on **one** append-only
tape so their receive clocks are directly comparable:

| `feed` | source | content |
|---|---|---|
| `clob_rest_book` | `clob.polymarket.com/book?token_id=` | one outcome token's **full ladder** (verbatim venue payload), one row per token per sweep |
| `data_api_activity` | `data-api.polymarket.com/activity?user=…&type=TRADE` | L5's public activity rows, verbatim, deduped by (txhash, ts, asset, size) |
| `pmus_gateway_book` | `gateway.polymarket.us/v1/markets/{slug}/book` | one PMUS outcome market's ladder (tennis/soccer lane) |
| `discovery` | — | the slate, each market's tokens, **why** it was admitted, scheduled vs. capped rate |
| `heartbeat` | — | per-stream row counts, token counts, 429 count, pacer state |
| `supervisor` | — | `cycle_error` rows: a transient failure goes **on the tape**, never into silence |

Nothing here is ever a fill. Every row carries
`labels: "BOOK/ACTIVITY OBSERVATION ONLY -- never a fill, never realized,
never an order"`. Per the CLAUDE.md accounting lifecycle this tape can support
labels no stronger than **shadow**.

---

## 4. Schema

`schema_version: "l5_book_shadow/1"`. Envelope on **every** row:

```json
{
  "schema_version": "l5_book_shadow/1",
  "kind": "book",                        // book|book_error|l5_activity|discovery|heartbeat|cycle_error
  "recv_utc": "2026-10-07T18:22:41.903184+00:00",
  "recv_unix": 1791742961.903184,
  "recv_monotonic_ns": 882391044123,     // the ordering clock (never steps)
  "venue": "polymarket",                 // polymarket|pmus
  "market": "btc-updown-15m-1791742800",
  "token": "7132…",                      // THE token/outcome identity
  "outcome": "Up",
  "feed": "clob_rest_book",
  "selector": "l5_active",               // why this market is on the tape
  "labels": "BOOK/ACTIVITY OBSERVATION ONLY -- …"
}
```

`kind: "book"` adds:

```json
{
  "outcome_index": 0,
  "sibling_tokens": ["7132…", "9981…"],   // the pair, so a MISSING token is detectable
  "complement_derived": false,            // true ONLY where the venue does not quote the second outcome separately
  "selector_why": "l5 public activity row at ts=1791742933 (condition=0x…)",
  "book": { "...": "venue payload, verbatim" }
}
```

`kind: "book_error"` carries `error: "<ExcType>:<message[:160]>"` and **no**
book. An error row counts toward `tokens_attempted`, never toward
`both_tokens` coverage: two failures must not read as "both tokens recorded".

`kind: "l5_activity"` carries `raw` (the venue row, verbatim) and
`venue_event_time` (the feed's own timestamp, in **its** granularity — see §7).

Derived views live in `derived/` and are computed **from** the raw tape:
`derived/token_coverage.json` is `verify_both_tokens(...)` — per market, which
token identities reached the tape, book-row counts, and the defective list.

---

## 5. Provenance guarantees

1. **Append-only.** `TapeWriter.MODE == "a"`, hourly parts
   `raw/tape_YYYYMMDDTHHZ.jsonl`, one `json.dumps` line per row, flushed per
   write. There is no update, truncate, rewrite, rotate-in-place or dedupe
   pass over a raw part anywhere in the module. A test asserts every `open(`
   in the writer uses `self.MODE` (or `"r"`), and another asserts earlier
   bytes are still a prefix after later writes.
2. **Derived ≠ raw.** Views go to `derived/`, a different directory, as
   separate files. The raw parts are immutable once written. *(The S3 cohort
   artifact in this repo was destroyed once by a process that treated its raw
   output as scratch; that is why this is a structural rule and a test, not a
   convention.)*
3. **Self-describing rows.** Receive wall clock, receive monotonic clock,
   venue, market, token/outcome identity, feed, schema version and selector
   are on every row — a part file read in isolation, years later, needs no
   external key.
4. **Failures are evidence.** Fetch errors, discovery errors and cycle
   exceptions are rows. The tape distinguishes "no data" from "no liquidity",
   which is the distinction this programme keeps losing.

---

## 6. Read-only and structurally incapable of ordering

* HTTP: `ReadOnlyFetcher` is the only HTTP object, issues `method="GET"`, and
  has no `post`. No signing path, no account endpoint, no credential needed.
* Websocket: `guard_outbound` admits `subscribe` / `unsubscribe` /
  `heartbeat` only and raises `PermissionError` on anything else.
* **Boot gate** (`boot_gate`, same rule as
  `scripts/l5_exec1_boot_preflight.py`): the process exits **2 before issuing
  a single request** if any of `src.l5_pmus_transport`,
  `src.pmus_q1_executor`, `src.pmus_micro_executor`,
  `analysis.l5.live_exec*`, `analysis.l5.live_micro` is **importable**.
  "Cannot" outranks "will not". In the monorepo the gate therefore **fails by
  design** — the deploy image (`deploy/l5_book_shadow/Dockerfile`) copies two
  files and nothing else, and passes. Verified both ways in tests and by
  running the slim tree.
* Credentials: `credential_presence()` returns booleans. No value is read into
  a row, a log line or the gate output; a test sets a sentinel secret in every
  credential env and asserts it appears in neither the tape nor the logs.
* No trading, now or later — stated in the module docstring, asserted by a
  test.

---

## 7. Sub-second ordering: what the feeds can and cannot support

This is the honesty note the brief asked for. **The collector can order
book-vs-book sub-second. It cannot order L5's *fill* sub-second from the
activity feed alone.**

What holds:

* **Our receive clock is sub-second and monotonic.** Every row carries
  `recv_monotonic_ns`. Two book rows, and a book row against an activity row,
  are orderable in **our** reception order to nanosecond resolution within one
  process. That is a genuine, usable ordering of *when we learned* things.
* **The two tokens of one market are polled back to back** in one sweep, so
  the intra-pair gap is one request slot (≈0.25 s at the shipped 240 rpm
  ceiling, less at higher budgets), and that gap is measurable from the rows
  themselves rather than assumed.

What does not hold, stated plainly:

1. **`data-api` activity timestamps are whole seconds.** L5's public activity
   rows carry a unix-second `timestamp` (the same granularity the historical
   corpus work repeatedly hit — see `analysis/l5/microstructure/LADDER_MODEL.md`,
   "block timestamps are whole seconds"). So *venue-time* ordering of "quote
   crossed" vs. "L5 filled" **inside the same second is not available from
   this feed, ever.** No amount of polling fixes it.
2. **Our receive time is not the fill time.** An activity row reaches us
   publish-latency after the fill (seconds, variable, and measured per row only
   as the gap between `venue_event_time` and `recv_utc` — which mixes venue
   publication delay with our poll phase and cannot be decomposed). Ordering
   two rows by `recv_monotonic_ns` orders **our observations**, not the venue
   events.
3. **REST book polling is sampled, not event-driven.** A quote that appeared
   and vanished between two sweeps is invisible, and a crossing is located only
   to within one poll interval. This bounds "crossed before the fill" to poll
   resolution, not to tick resolution.

What this means for the artifact, and what would remove the limit:

* The question **"was the complement price reachable at all in the seconds
  around the turn, and on which token's book"** is answerable from this tape
  today. That is the lane-blocking question.
* The strict question **"did the opposite quote cross strictly before the fill
  within the same second"** is **not** answerable from the public activity
  feed, at any polling rate. Answering it needs an **event-stamped trade
  feed**: Polymarket's CLOB market websocket (`book` / `price_change` /
  `last_trade_price` on both `assets_ids`) carries venue-side millisecond
  stamps, and an own-account fill record carries exact fill times for *our*
  orders. The module is structured for that upgrade — `guard_outbound` and the
  `feed` field already exist, and a `clob_ws_book` feed drops in alongside
  `clob_rest_book` without touching the schema — but **it is not implemented
  here, and the tape must not be read as if it were.** Rows say which feed
  they came from precisely so this distinction survives into analysis.

Analysis built on this tape must therefore state its resolution as
**±(one poll interval) in our receive order, and ±1 s in venue time**, and
must not claim a strict sub-second venue ordering of L5's fills.

---

## 8. Market selection (one collector, configurable selector)

`BOOK_SHADOW_SELECTORS` picks any combination of:

* **`l5_active`** — markets L5 is actively trading **and** that we can
  identify in realtime: L5's public activity rows give the slug, the slug
  resolves on `gamma-api` to the market object, and its `clobTokenIds` name
  **both** outcome tokens. A row we cannot resolve is skipped and counted,
  never guessed.
* **`series`** — a named list of series/categories, `BOOK_SHADOW_SERIES`,
  defaulting to **`tennis,soccer`** (requirement 5: the pair-cost lane that
  needs sampling at scale, per `analysis/l5/opportunity/CATEGORY_RANKING.md`,
  where tennis's 0.9477 and soccer's 0.7636 are the only evidenced pair costs
  inside spring discipline). On PMUS the two outcome **markets** of a live
  event are the two independent books; on Polymarket the two `clobTokenIds`
  are. An event exposing only one market is recorded with
  `complement_derived: true`, so the tape says what it does not have.

Both the selector and its reason land on every row (`selector`,
`selector_why`) and on each `discovery` row, so a tape always says **why** a
market was recorded.

---

## 9. Pacing (safety requirement)

One `Pacer` for the **whole process** — every book poll, every activity poll
and every discovery call. Adding markets degrades **cadence**, never raises
the venue rate, and no token is silently dropped.

* Ceiling: `BOOK_SHADOW_MAX_RPM`, default **240 requests/minute total**.
  That is the **worst case**, documented in the module docstring.
* HTTP 429 → `Pacer.penalize()` doubles the interval process-wide (cap 30 s);
  a clean heartbeat period relaxes it back toward the configured value. *(An
  unpaced crawl in this repo once starved a live order preflight into 429 and
  halted a campaign with a leg held — hence a shared ceiling rather than
  per-stream intervals.)*
* The schedule it asks for is reported on every discovery and heartbeat row
  (`scheduled_rpm` vs. `pacer_worst_case_rpm`), so over-subscription is
  visible rather than silent.

---

## 10. Self-recovery

* Discovery failure **keeps the previous slate** and records
  `kept_previous_slate: true`.
* A book fetch failure writes `book_error` and the sweep continues.
* Any cycle exception writes `cycle_error`, sleeps briefly, continues. The
  loop exits only on an explicit `--minutes` budget or SIGINT.
* `--minutes 0` (the deployment default) = forever, the convention every
  sibling collector in `deploy/` uses.
* The heartbeat carries **per-stream** row counts, so one stalled stream is
  visible while the others are healthy, plus `single_token_markets`, which
  must stay empty.
