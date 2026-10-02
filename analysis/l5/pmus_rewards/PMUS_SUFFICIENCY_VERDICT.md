# PMUS data sufficiency for queue-aware two-sided replay — **FAILS**

2026-10-02. No orders. No new service. Gemini PAIR_OR_UNWIND frozen at
`cd8e2452` and not optimized further.

## Verdict

**The recorder acceptance chain cannot be closed on data reachable from this
environment.** PMUS has the *best* order book of any venue examined and the
*worst* trade visibility.

    venue trade  ->  captured trade  ->  valid preceding L2  ->  queue state
         OK              FAIL              PARTIAL                 FAIL

## L1 — captured trade: **FAIL**

**The authenticated collector already exists and is the right tool.**
`src/pmus_ws_evidence_recorder.py` subscribes `SUB_MARKET_DATA = 1` and
`SUB_TRADE = 3` on `wss://api.polymarket.us/v1/ws/markets`, signing
`{ts}GET/v1/ws/markets` with Ed25519. Nothing needs building.

It cannot be run here:

- the WS rejects an unauthenticated handshake with **HTTP 401**;
- no `PMUS_*` / Polymarket credentials are present in this environment, the
  same gap that blocks the historical Gemini reconstruction.

Six public trade paths probed, all **404**: `/markets/{slug}/trades`,
`/trades?market_slug=`, `/prints`, `/history`, `/fills`, `/ticks`.

### A correction to my own first reading

I initially wrote that the public `stats` block carries "only cumulative
counters". **That is wrong** — it carries `lastTradePx`, `lastTradeQty` and
`lastTradeSetTime`, so one print per cache refresh *is* visible. The failure is
quantitative, and I measured it rather than asserting it:

| | |
|---|---|
| interval between two observed states | **30 s** |
| `sharesTraded` moved | 236,344.21 → 257,821.96 = **21,477.75 shares** |
| `lastTradeQty` reported | **1.17 shares** |
| **fraction of traded volume visible** | **0.0055%** |

One 1.17-share print is reported while 21,478 shares trade. Individual trades
cannot be enumerated, and **no field on any surface carries an aggressor
side**.

## L2 — valid preceding L2: **PARTIAL**

Content is excellent, cadence is not.

| | |
|---|---|
| median bid levels | **32** |
| median ask levels | **29** |
| polls | 40 over 46.5 s at 1/s |
| **distinct book states** | **2** |
| effective update interval | **~46.5 s** |

PMUS publishes genuine full depth — far better than the Gemini canonical tape,
which has touch size only. But the REST surface is cached at tens of seconds,
so there is no *fresh* preceding book to bind a trade to. Depth passes on
content and fails on timeliness.

## L3 — queue state reconstructable: **FAIL**

Queue-ahead needs the resting size at our price (available, L2 content) **and**
which side each trade consumed (unavailable). Without an aggressor side and
without trade enumeration, queue consumption is **UNKNOWN**. Under the
unchanged fill rule an unknown-side trade fills nothing, so every fill would
be zero — the same artifact that made the first Gemini WS tape produce a
confident-looking zero.

## What makes this genuinely frustrating

PMUS BTC15m is **far more liquid than Gemini**. One 15-minute window traded
**236,344 shares / $117,200 notional**, against a Gemini BTC15M median of
**134 contracts**. The venue where the strategy might actually have capacity is
the venue whose trade tape we cannot read.

Also observed: PMUS lists **one open window at a time** (0345z open, 0330z and
earlier `MARKET_STATE_EXPIRED`), so a prospective shadow would accumulate at
roughly 96 windows/day with no backfill path.

## PAIR_OR_UNWIND on PMUS: not run, and why that is the correct outcome

The pre-registered ceilings (.99/.98/.97/.95) and the requirement to keep
complement acquisition and original-leg sale as **separate execution paths**
are both preserved in the design and unexecuted. Running it on this data would
require inferring the aggressor side from a 46-second-stale quote, which is
exactly the fill-model loosening that is not permitted. **No result is better
than a result built on an inferred side.**

Worth noting for when the data exists: PMUS quotes `lastPriceSample` with
`longPx` **and** `shortPx` (0.78 / 0.22), so unlike Gemini it does express both
sides of the pair — the separate-execution-paths requirement is meaningful
there in a way it is not on Gemini's single `-UP` instrument.

## Exactly what unblocks it

One thing, and it is not a build:

> **PMUS API credentials (`X-PM-Access-Key` + Ed25519 secret) in an
> environment Claude can use.** The collector, the subscribe frames, the auth
> scheme and the PAIR_OR_UNWIND simulator all already exist.

With credentials the chain closes at L1 and L3 immediately: `SUB_TRADE = 3`
supplies trades and `SUB_MARKET_DATA = 1` supplies the ladder on the same
stream, synchronized by construction — the property that made the Gemini
companion-poller workaround necessary and that PMUS would not need.

Without them, PMUS remains **untestable**, not unpromising.
