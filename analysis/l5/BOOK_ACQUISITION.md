# Historical Polymarket book/L2 acquisition for L5's active period (2026-03-19 → 2026-08-31)

P0 data task. The question this data must answer: around each actual L5 fill, at
**−5s / −1s / fill / +1s / +5s / +10s / +30s / +60s**, what was the resting book?
Without it we cannot separate (a) L5 executing against genuinely available
two-sided liquidity from (b) an artifact of seeing only his own fills.

Evidence grades used throughout: **VERIFIED-ON-DISK**, **REACHABLE-BUT-UNTESTED**,
**DOCUMENTED-ONLY**, **UNAVAILABLE**. No book state is estimated, interpolated or
synthesised anywhere below.

---

## 1. Verdict

**Some book data exists, and it is tiny: 3.5 days of the 166-day span (2.1%).**

The only historical Polymarket L2 on disk anywhere in this repository or its git
history is the Zenodo capture of **2026-08-21 → 2026-08-24**. Everything else that
matches `book|l2|depth` is either prospective (Sept/Oct 2026 recordings, after
L5's period) or a different venue (Polymarket US, Kalshi).

For the remaining 162 days there is **no free lawful route**. The official
Polymarket CLOB has no historical-book endpoint that covers the period: the one
endpoint that ever served past books, `clob.polymarket.com/orderbook-history`,
**stopped producing snapshots on 2026-02-20 ~20:00 UTC** — before L5's period
begins. On-chain Polygon logs give **fills and order events, never a resting
book**. Full-span book history therefore requires **a commercial vendor** (several
claim coverage that brackets the whole period) or prospective capture, which
cannot retroactively cover March–August.

Two concrete gains need no vendor and no new access:

1. **184 L5 5-minute fills across 38 markets are already joinable on disk** and
   have never been analysed — prior work was BTC**15m** only.
2. A **free CC BY 4.0 dataset (Marketlens) covers BTC 5m/15m/hourly on
   2026-06-15**, a day on which **2,936 L5 BTC15m fills are already on disk** —
   an independent mid-period day that would out-of-sample the whole Zenodo
   BBO-offset result. Download is currently blocked by this container's egress
   (documented in §4), not by licence or cost.

---

## 2. What is on disk — exhaustive search result

Searched: full worktree, `data/`, `analysis/`, `docs/`, root `*.md`, untracked
and gitignored paths, the scratchpad, and **all git history**
(`git log --all --diff-filter=A --name-only`) for `clob`, `/book`, `orderbook`,
`order_book`, `midpoint`, `/prices`, `bids`, `asks`, `depth`, `level2`, `l2`,
`tick`, `candle`, websocket subscriptions and any Polymarket CLOB client.

### 2.1 The one historical L2 holding — VERIFIED-ON-DISK

Zenodo record **22084977** (`polyorderbooks`, DOI 10.5281/zenodo.22084114,
CC BY 4.0), three parquets, present at
`…/scratchpad/l2/updown_{5m,15m,4h}.parquet` (45 MB), downloaded 2026-10-04.

| file | rows | markets | captured_at range (UTC, measured) |
|---|---:|---:|---|
| `updown_5m.parquet` | 167,941 | 471 | 2026-08-21 00:15:48 → 2026-08-24 11:44:59 |
| `updown_15m.parquet` | 239,310 | 265 | 2026-08-21 00:14:09 → 2026-08-24 12:29:59 |
| `updown_4h.parquet` | 489,941 | 69 | 2026-08-21 03:56:08 → 2026-08-24 11:59:59 |

This is a **true resting book**, not trades: `bid_prices`/`bid_sizes` and
`ask_prices`/`ask_sizes` are full aligned ladders (measured median 39/46/9 bid
levels; max 107), plus `best_bid`, `best_ask`, `crossed`, `seconds_to_close`,
`token_id`, settled `winner`. 8 coins (btc, eth, sol, xrp, bnb, doge, hype, zec).

Granularity caveats, from the dataset's own README and carried into the existing
analysis: a row is written only **when the book changes** (an old row is current
state, not staleness), the feed carries ~24k events/s with **no sequence
numbers** so dropped deltas are undetectable, and the collector REST-resyncs
every **60 s** — so every offset carries up to one 60 s resync of possible drift.

### 2.2 Exact joinable overlap with L5 — measured, not assumed

L5's own fills on disk (`data/l5/raw/trades.jsonl`, `activity.jsonl`,
`activity_gap.jsonl.gz`): **778,825 TRADE rows**, 15m 714,043 / 5m 64,782, over
**128 of the 166 span days**. L5 trades only **btc** (85,224) and **eth** (3,266)
and only **15m and 5m** — never 4h, so `updown_4h.parquet` is worthless for him.

Joining by exact `market_slug`:

| contract | L5 fills joinable | distinct markets | status |
|---|---:|---:|---|
| 15m | 3,074 | 27 (of 33 btc15m in file) | already analysed |
| 5m | **184** | **38** | **never analysed** |
| 4h | 0 | 0 | L5 never trades 4h |

**Correction to `analysis/l5/historical_l2/ZENODO_OVERLAP_FINDINGS.md`.** That
document states "on our own file the overlap with this dataset is **zero**" and
therefore reconstructs L5's fills from Polygon archive logs
(`backfill.py`, 3,166 fills / 12 windows). That was true of the canonical BTC15m
history, but **`activity_gap.jsonl.gz` already holds L5's fills for those days** —
24,705 on 08-21, 6,745 on 08-22, 0 on 08-23, 16,069 on 08-24 (130 days total,
2026-04-02 → 2026-08-28, 706,500 rows, with `slug`, `conditionId`, `outcome`,
`outcomeIndex`, `price`, `size`, `timestamp`, `transactionHash`). The chain
backfill's conclusions are not impeached — its independent count agrees in
magnitude — but the overlap did not require a chain walk, and the **5m** slice it
never covered is sitting on disk. I have not modified that file.

### 2.3 Everything else matching the search terms — none of it covers the period

- `analysis/l5/historical_l2/` — the Zenodo analysis itself (`bbo_offset.py/json`,
  `mint_match`, `pre_fill_complement`, `backfill.py`; in history also
  `queue_forensics`, `book_duality`, `mint_onchain`). Derived from §2.1, same 3.5 days.
- `analysis/l5/sync_l2/FIRST_WINDOWS_READING.md` (git history, commit `617e8f5d`) —
  a **native CLOB book feed** capture with millisecond `transaction_hash` join
  (12/12 fills joined; `price_change` delta semantics validated at 0.9993 on
  57,738 observations). Real book state, real method — but **2026-10-01**,
  prospective, outside the period.
- `analysis/pmus/evidence/auth_book_tape.jsonl`, `AUTH_BOOK_FIRST_LIVE_WINDOW.json`,
  `NO_BOOK_INVARIANT_2026-09-23.json`, `data/pmus_book_tape/*`,
  `data/pmus/sports_incentives_2026-09-10/depth.json` — **Polymarket US**,
  2026-09/2026-10. Wrong venue and after the period.
- `analysis/gemini/microstructure/p0c_print_vs_book*`, `p2_pretrade_book.json`,
  `p9e_book_tape.py`, `book_schedule_proof_*.json`, `src/kalshi_live_book.py`,
  `src/l5_book_context.py`, `src/l5_live_book_feed.py`,
  `scripts/gemini_book_evidence_poller.py` — live/prospective collectors and
  Kalshi/Gemini work. No historical Polymarket book payloads.
- `analysis/l5/v1_causal_public_l2.json` (history, `88788e1d`) — simulator output
  labelled `fill_evidence: "INFERRED (no individual prints)"`. Not book data.
- `analysis/l5/venue/CLOB_EXECUTION_PATH_SURVEY_2026-09-17.md` — confirms every
  credential held authenticates to **Polymarket US**, not the international CLOB
  whose books we need; `clob.polymarket.com` appears only in read helpers, zero
  commits ever touched `py_clob_client`/`ClobClient`.

**No partial download, cached response, fixture or abandoned book fetch covering
2026-03-19 → 2026-08-31 exists anywhere in the repo or its history.**

---

## 3. Fills are not a book — the distinction that decides this task

Three of the candidate routes return **transactions**, not **resting liquidity**:

- **Polygon on-chain logs** (`eth_getLogs` on the CTF Exchange, the method
  `analysis/l5/historical_l2/backfill.py` already uses against
  `https://polygon.drpc.org`) give **fills**, and order *placement* is off-chain
  on Polymarket, so unfilled resting orders and cancellations **never reach the
  chain at all**. On-chain can tell us who filled at what price and whether L5
  was maker or taker. It can **never** tell us what size was resting at −1s.
- `data-api.polymarket.com/trades`, `/activity` — prints only, and L5-only here.
- Any "trades/tick" vendor tier — prints only.

Only a **snapshot/delta book archive** answers the brief's −5s…+60s question.
Marked **BOOK** vs **FILLS-ONLY** in §5 accordingly.

A second, already-established caution: the two tokens' books are mechanically
complementary (`best_ask(X) + best_bid(complement) = 1.0000` exactly), so
"mint feasibility" tests built on that sum are tautological, and anonymous depth
cannot tell us **which orders were L5's**. More book history widens coverage; it
does **not** turn anonymous depth into ownership. Separating reactive from
both-sides-first still needs the prospective experiment where our own orders are
identifiable.

---

## 4. Reachability actually tested — exact hosts and statuses

All attempts via the pre-configured proxy (`HTTPS_PROXY=http://127.0.0.1:36553`,
CA bundle `/root/.ccr/ca-bundle.crt`). TLS verification left on; `HTTPS_PROXY`
never unset; no block circumvented, no VPN, no alternate route attempted.

`curl -sS -o /dev/null -w '%{http_code}' --max-time 15 https://<host>/`,
2026-10-06:

| host | result |
|---|---|
| `clob.polymarket.com` | **403 to CONNECT** (`curl (56) CONNECT tunnel failed, response 403`) |
| `data-api.polymarket.com` | **403 to CONNECT** |
| `gamma-api.polymarket.com` | **403 to CONNECT** |
| `zenodo.org` | **403 to CONNECT** |
| `huggingface.co` | **403 to CONNECT** |
| `datasets-server.huggingface.co` | **403 to CONNECT** |
| `www.kaggle.com` | **403 to CONNECT** |
| `api.dune.com` | **403 to CONNECT** |
| `api.goldsky.com` | **403 to CONNECT** |
| `polygon.drpc.org` | **403 to CONNECT** |
| `polygon-rpc.com` | **403 to CONNECT** |
| `api.tardis.dev` | **403 to CONNECT** |
| `api.amberdata.com` | **403 to CONNECT** |
| `cryptostruct.com` | **EGRESS_BLOCKED** (fetch tool) |

`$HTTPS_PROXY/__agentproxy/status` reports `enabled: true`,
`bundleCoversEveryHost: true`, and `recentRelayFailures` of the same
`connect_rejected / gateway answered 403 to CONNECT` kind for other hosts. So the
proxy is healthy and this is **policy denial, not misconfiguration**.

**This is a change, and it matters for planning.** `zenodo.org` and
`polygon.drpc.org` both *worked from this container on 2026-10-02 → 10-04* — that
is how the parquets and `l5_backfill.json` got on disk. Both are 403 today.
Treat the general-purpose search/fetch channel (which does work — §5 claims were
read through it) as separate from bulk egress: **no bulk download of any
candidate source can be performed from this container right now.**

---

## 5. Ranked acquisition plan

Ranked by evidence-per-unit-friction for the −5s…+60s measurement.

### Rank 1 — Finish the 5m slice of the Zenodo data already on disk
**BOOK · VERIFIED-ON-DISK · free · zero egress · hours of work**
184 L5 5-minute fills across 38 markets, full ladders at 1 s. Reuses
`bbo_offset.py` with the 5m parquet and `activity_gap.jsonl.gz` as the fill
source (`transactionHash`/`slug` join, never market name).
*Gives:* the full offset grid at −30/−5/−1 s and the +1…+60 s legs for 5m, and the
first test of whether the 15m BBO-offset shape is a property of prediction markets
or of *15-minute* prediction markets — the comparison the dataset was built for.
*Does not give:* order ownership; n=184 is small, so report it as a shape check,
not a decision input. Coverage stays 2.1% of the span.

### Rank 2 — Marketlens free archive, 2026-06-15
**BOOK · DOCUMENTED-ONLY (read via fetch tool; download blocked) · free, CC BY 4.0**
`github.com/marketlenstrade/polymarket-historical-data` — 2,278 markets / 7 events
/ 17.3M rows; the **Bitcoin 5m/15m/hourly** folder is dated **2026-06-15**
(111.2 MB), tick-by-tick `history-<market>-compact.parquet` carrying
**snapshots, deltas and trades**, ladders as `{"price","size"}` lists best-first.
**2,936 L5 BTC15m fills are already on disk for that exact day.**
*Gives:* an independent mid-period day — a genuine out-of-sample replication of the
Zenodo BBO-offset finding, and because it carries **deltas**, potentially finer
than 1 s resolution around each fill.
*Does not give:* ownership; one day only (raises coverage to ~3.0%).
*Needs:* egress to github.com (403 today). Verify the stated date and the 15m
contract set on download — the date is read from the repo page, not yet from the
files. Attribution "Marketlens, https://marketlens.trade" is required.

### Rank 3 — Commercial full-span vendors
**BOOK · DOCUMENTED-ONLY · paid · the only route that covers 162 missing days**
Claims read via the search/fetch channel; **none verified**, all pricing and
coverage to be confirmed in writing before purchase.

| vendor | claimed coverage | claimed granularity | access |
|---|---|---|---|
| **Resolved Markets** | full-depth books **since March 2026** — brackets the span | 1 s full L2 ladders on resolved markets; `condition_id`, `token_id`, `event_timestamp`, `capture_timestamp`, `sequence_number`, best bid/ask, mid, spread, full bids/asks arrays | REST, OpenAPI, CLI export, MCP |
| **CryptoStruct** | Polymarket ticks **since July 2025** | every trade + full-depth L2 | per-instrument-day, ~€1/day, instant download |
| **PMData** | **since February 2026** | tick-by-tick book snapshots + updates + trades, Up/Down markets | 200k+ markets, 1TB+ |
| **Marketlens (paid)** | full archive | tick L2 snapshots **and deltas**, ms resolution | Python SDK, REST, bulk Parquet |
| **Telonex** | — | trades, book changes, quotes, onchain fills | daily Parquet via API |
| **DepthFeed** | event-driven CLOB updates | normalised full-book observations | REST + WebSocket |

Resolved Markets' stated start (March 2026) and CryptoStruct's (July 2025) are the
two that could cover 2026-03-19 → 08-31 end to end; CryptoStruct's per-day pricing
makes a cheap targeted buy possible — **L5 traded only btc and eth, only 5m and
15m, on 128 known days**, so the purchase can be scoped to those instrument-days
rather than the whole venue. `sequence_number` (Resolved Markets) would also fix
the one weakness of the Zenodo data: undetectable dropped deltas.
*Gives:* the actual brief — book state at −5s…+60s across the whole period.
*Does not give:* order ownership (no public archive can attribute orders to L5).
*Needs:* payment, terms review, and egress.

### Rank 4 — Polygon on-chain backfill
**FILLS-ONLY · REACHABLE-BUT-UNTESTED (worked 10-02→10-04, 403 today) · free**
Proven code already in-repo (`analysis/l5/historical_l2/backfill.py`, ~50-block
`eth_getLogs` chunks, failed chunk recorded as a HOLE, real block timestamps).
*Gives:* complete maker/taker-attributed fill reconstruction for any day in the
span, independent of our collection gaps — valuable, and the right tool for the
38 span days where we hold no L5 activity.
*Does not give:* **any resting book.** Placement and cancellation are off-chain.
This route cannot answer the brief's question, and must not be presented as doing so.

### Rank 5 — Official Polymarket CLOB `/orderbook-history`
**BOOK in principle · UNAVAILABLE for our period**
`clob.polymarket.com/orderbook-history?asset_id=&startTs=&endTs=&limit=500&offset=`
**stopped producing snapshots 2026-02-20 ~20:00 UTC**; windows after the cutoff
return `{"count":0,"data":[]}` (pre-cutoff, e.g. 2026-02-12, still returns data).
L5's period starts **2026-03-19**, entirely after the cutoff. `GET /book` returns
the **current** book only and Polymarket's docs list no endpoint for past bids and
asks. Dead end — but worth one confirming probe when egress returns, since the
report is third-party.

### Rank 6 — Dune / Goldsky / subgraphs
**FILLS-ONLY · DOCUMENTED-ONLY** Indexed Polygon events — same off-chain
limitation as Rank 4, with an extra indexing layer. Convenient for fills, no book.

### Rank 7 — Tardis.dev / Kaiko / Amberdata / CryptoTick
**Centralised-crypto-exchange vendors.** No evidence any carries Polymarket CLOB
books. De-prioritised unless a specific Polymarket product is confirmed.

### Rank 8 — Hugging Face / Kaggle / academic replication sets
**UNVERIFIED.** The only prediction-market book dataset found in the open-data
space is the Zenodo/polyorderbooks one already held (§2.1) plus the Marketlens
GitHub set (Rank 2). Zenhodl.net is listed as offering Polymarket/Kalshi depth
parquets — unverified, worth a look when egress returns. Cheap to re-check; do not
plan around it.

### Not a route — prospective capture
The native-feed pipeline (§2.3, ms-accurate, delta semantics validated) is the
best book instrument we have and the only one that can ever establish order
ownership. It **cannot** cover 2026-03-19 → 08-31. It belongs to the prospective
experiment, not to this acquisition question.

---

## 6. Bottom line

- **Does book data covering the period exist on disk?** Yes, for **3.5 of 166 days
  (2.1%)**: Zenodo 2026-08-21→24, full L2, verified. Nothing else, in the worktree
  or anywhere in git history.
- **Free next steps that need no vendor:** the unexploited **5m** slice already on
  disk (184 L5 fills, 38 markets), then the free CC BY 4.0 Marketlens day
  **2026-06-15** against **2,936** L5 fills already on disk.
- **For the other 162 days: this needs a paid vendor.** The official CLOB's only
  historical-book endpoint went dark a month before L5's period began, and
  on-chain data gives fills, never a resting book. Resolved Markets (claimed from
  March 2026) and CryptoStruct (claimed from July 2025, ~€1/instrument-day) are the
  two candidates whose claimed coverage brackets the span; scope any purchase to
  btc/eth, 5m/15m, the 128 known L5 fill-days.
- **No public archive, at any price, attributes orders to L5.** Book history tells
  us whether the liquidity was there. It cannot tell us which of it was his.
