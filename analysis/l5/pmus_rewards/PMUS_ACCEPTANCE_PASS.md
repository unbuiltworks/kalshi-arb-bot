# PMUS acceptance chain — **PASSES** on the existing authenticated recorder

2026-10-02. No orders. No new service. No collector built. Nothing deployed or
restarted. No secret value read, printed, copied or committed.

This supersedes `PMUS_SUFFICIENCY_VERDICT.md`, which concluded FAIL from the
public REST surface. **I was looking in the wrong place.** The working
authenticated recorder already exists, already holds credentials, and was
already running.

## Credentials — presence only

| | |
|---|---|
| Project | `arb-ev-wallet-shadow` (`50fb7357…`) |
| Environment | `production` (`f3642d27…`) |
| Service | **`pmus-ws-evidence-recorder`** (`71b6ea99…`) |
| `PMUS_API_KEY_ID` | **PRESENT** |
| `PMUS_API_SECRET_KEY` | **PRESENT** |
| Values visible to me | **No** — Railway returned `valuesRedacted: true`; names only |
| Sealed variables | none |

Also present and relevant: `PMUS_WS_TRADES_LOG`, `PMUS_WS_TRADES_HTTP`,
`PMUS_WS_BOOK_LOG`, `PMUS_WS_BOOK_LOG_MARKETS`, `PMUS_WS_PIN_SLUGS`,
`PMUS_WS_SERIES`. A 50 GB volume is mounted at `/state/pmus-ws`.

Checked and **without** credentials: `pmus-btc15m-tape` (`1f0c6aaf…`) carries
only Railway defaults.

## Recorder health

Latest deployment **SUCCESS**, 2026-09-26T12:15:54Z. `get-status` reports
`numReplicas: null`, but that is misleading — **the service is live and
emitting now**, with log lines at 2026-10-02T04:04:57Z–04:05:38Z. **Nothing was
started or redeployed.**

One unrelated observation, reported not acted on: the environment carries a
**staged `EnvironmentPatch` pending since 2026-09-24T20:29:56Z**. I did not
commit, apply or discard it.

## Why the chain closes here and not on REST

The recorder emits two row types. The trade row is the whole reason:

    trade: t, px, qty, trade_time, bid_at_trade, ask_at_trade, dir
    book:  t, bid, ask, bid_qty, ask_qty, status

- **`dir` is the taker direction** — the consumed side is *read*, never inferred.
- **`bid_at_trade` / `ask_at_trade` is the authenticated top of book at the
  instant of the trade**, bound by the recorder itself. The
  trade→preceding-book synchronization therefore needs no merge and carries no
  clock-skew risk — the thing the Gemini companion-poller had to work around.
- `bid_qty` / `ask_qty` is displayed depth at that state: **queue-ahead for a
  maker resting AT the touch.**

## Acceptance result — 8 BTC15m windows, 16,620 trades

Harvested from the recorder's own logs; nothing interpolated or backfilled;
deduped by (slug, t, px, qty) so repeated pulls cannot double count.

| outcome | n | % |
|---|---|---|
| **SCORABLE** | **11,624** | **69.9%** |
| UNSCORABLE — book older than 2 s | 4,341 | 26.1% |
| UNSCORABLE — no preceding book row | 402 | 2.4% |
| UNSCORABLE — `dir` unknown | 253 | 1.5% |

`book_age_ms` for scorable trades: **p50 69 ms**, p90 1,243 ms, max 2,000 ms
(threshold 2,000 ms, the same one used on Gemini).

Per window, scorable share runs **61.3%–74.5%** across 7 windows of ~900 s on
2026-09-30 plus one partial window on 2026-10-02.

    venue trade  →  captured trade  →  synchronized preceding L2  →  side/queue
         OK              PASS                   PASS                    PASS

**The chain passes.** UNSCORABLE trades are held as UNKNOWN and fill nothing;
none is read as a quiet market.

## Two limits, stated as limits

1. **No depth beyond the touch.** `bid_qty`/`ask_qty` is the touch only, exactly
   as on the Gemini canonical tape. A quote resting behind the touch has UNKNOWN
   queue and must not fill — the same constraint, already enforced.
2. **The book tape is capped and hitting its cap.** Every `PMUS_BOOK_ROWS`
   emission reports `rows: 50` against `cap_per_market: 50`, producing ~5 s
   holes between emissions and causing the 26.1% stale-book exclusions. Trades
   are *not* truncated (`rows` 54–110 against `cap_per_market: 200`).
   **Raising `PMUS_WS_BOOK_LOG`'s cap would recover most of that 26.1%** — a
   one-variable change to a live service, which I have **not** made.

## Capacity, for contrast

| | PMUS BTC15m | Gemini BTC15M |
|---|---|---|
| trades per ~900 s window | **1,387 – 3,046** | median **3** |
| scorable trades in hand | **11,624** | 735 total over 37 h |

PMUS produces more scorable trades in one 15-minute window than the entire
Gemini historical tape. This is the venue where PAIR_OR_UNWIND can actually be
tested.

## Next

Sufficient windows are already in hand for the pre-registered PAIR_OR_UNWIND
shadow (.99/.98/.97/.95), with complement acquisition and original-leg sale kept
as separate execution paths. No live orders.
