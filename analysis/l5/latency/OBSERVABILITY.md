# How late can we see L5? — measured, per surface

**Status: OBSERVABILITY_LIMIT confirmed at ~15 s median for every
Polymarket-published surface — but NOT a hard limit on observing L5. Reading
Polygon directly lands at ~3.8 s and beat the feed on 10 of 10 observations.**
Historical and out-of-sample behavioural modelling is unaffected. The limit is a
property of *publication*, not of consensus — which is exactly why a different
source escapes it.

Measured 2026-09-15, 9-minute simultaneous run, five public surfaces,
`analysis/l5/latency/probe.py` → `report.py`. Read-only, no credentials.

## The headline

| surface | p05 | p50 | p95 | max | n | identity? |
|---|---|---|---|---|---|---|
| `clob_ws` `price_change` (book) | 0.00 | **0.01** | 1.87 | 4.61 | 471,549 | no |
| `clob_book_rest` | 0.01 | **0.02** | 0.22 | 1.05 | 1,899 | no |
| `clob_ws` `last_trade_price` | 2.05 | **15.24** | 23.44 | 25.29 | 432 | no |
| `data-api /activity?user=L5` | 1.52 | **15.00** | 29.75 | 31.55 | 208 | **YES** |
| `data-api /trades?user=L5` | — | **225.6** | — | 225.6 | 2 | YES |
| `data-api /trades` (global) | — | — | — | — | **0** | YES |

**Book state is real time. Trade prints are not.** Three orders of magnitude
separate them, and the separation is the whole finding: we can know the *market*
contemporaneously and cannot know *L5* contemporaneously.

## A number that was load-bearing and wrong

The FIDELITY runner has been reporting a **28.5 s median** feed lag from twelve
samples, and that figure is the stated reason estimand B is served NOT READY.
At n=208 the median is **15.0 s**. The old figure was not a different
measurement — it was too few samples of a distribution whose shape makes small
samples misleading.

## The shape: a ~31 s batch refresh, not network latency

- 83% of discoveries arrive within 50 ms of another (171 of 207 inter-arrivals)
- p95 inter-arrival **30.1 s**
- observed mean 15.29, median 15.00, max 31.55

A batch refresh of period *T* makes an event's lag uniform on [0, *T*]: mean and
median both *T*/2, max ≈ *T*. Observed mean 15.29 and median 15.00 against a max
of 31.55 fit **T ≈ 31 s** almost exactly. So the lag is *where in the refresh
cycle the fill landed*, which is why single samples vary so wildly — one L5 fill
was observed 4.5 s after its source timestamp, another 31.5 s.

This matters for interpretation: the lag is **not** reducible by polling harder.
We already poll at 1 Hz. It is a property of the publisher.

## Realtime replicability ceiling

An anchor learned *L* seconds late leaves *h* − *L* to act; any horizon *h* ≤ *L*
is already in the past when we learn of it. Empirically, over 208 fills:

| horizon | knowable in time | ceiling |
|---|---|---|
| 1 s | 6 / 208 | **2.9 %** |
| 5 s | 28 / 208 | **13.5 %** |
| 15 s | 109 / 208 | **52.4 %** |
| 30 s | 198 / 208 | **95.2 %** |

This is a ceiling on *knowing*, before any question of reacting, routing or
filling. It is not a model result and no model change can raise it.

## Can L5 be reconstructed from a faster stream? Tested, not assumed

- **`/trades` carries `proxyWallet` even unfiltered** — the global feed *is*
  identity-bearing. But L5 never once appeared in its top 500 across the run:
  site-wide volume buries him. Not a usable surface.
- **`clob /trades` returns 401.** Not public.
- **The websocket trade print carries `transaction_hash`** — the *same exact
  key* `/activity` uses. The surfaces join exactly, no fuzzy matching needed.
- **Keyless public Polygon RPCs resolve that hash in p50 0.24 s**, and L5's
  address is present in the receipt logs **in an indexed topic**, so it can be
  filtered for directly. (Working keyless: `polygon-bor-rpc.publicnode.com`,
  `polygon.drpc.org`. Rejected: `polygon-rpc.com` 401, ankr key-required,
  `1rpc.io` quota, blockpi 521, blast 403.)
- **And yet attribution still lands at p50 15.56 s**, because the print itself
  is late — barely ahead of `/activity`'s 15.00 s.

### A wrong turn worth recording

I first measured `clob_ws` at p50 0.01 s and concluded reconstruction was
sub-second. **That was wrong.** 0.01 s is the p50 over *all* websocket events,
99 % of which are `price_change` book updates. The `last_trade_price` events —
the only ones that can carry a fill — sit at p50 15.24 s. The aggregate hid the
one subpopulation the question was about.

## Why it is late, and the one route still open

Measured on 14 trades, `block_timestamp − ws_source_timestamp` = **p50 2.9 s**
(p05 1.4, p95 3.5). So the websocket timestamp is the **match** time, and the
block carrying it lands ~3 s later. Yet the print is not published for ~15 s.

**About twelve of those fifteen seconds are publication delay, not consensus.**

That is a real opening, and it was measured rather than assumed.
`analysis/l5/latency/chainwatch.py`, n = 10 joined observations:

| | value |
|---|---|
| chain detection after block | p50 **0.87 s** (min −0.20, max 7.54) |
| chain earlier than `/activity` by | p50 **19.59 s** (min 2.15, max 34.09) |
| chain won | **10 / 10** |

Adding the measured ~2.9 s from match to block, total observability via the
chain is **≈ 3.8 s against ~15 s**, and it beat the feed on every observation.
At that lag the 5 s, 15 s and 30 s horizons all become knowable in time and only
h = 1 s stays out of reach.

Caveats stated rather than smoothed: the chain figures are bounded by the scan
loop's own 0.4 s cadence, so the true floor is lower; and the −0.20 s is
detection slightly ahead of the block's *declared* timestamp — a proposer-clock
artifact, not time travel, which does imply the real chain lag is near zero.

**This is a route, not a deployment.** Nothing has been built on it.

## What this does and does not invalidate

- **Event-time fidelity is untouched.** The state contains only fills at or
  before the anchor's own timestamp and the outcome is the next fill in event
  order, so a uniform publication delay cannot distort it.
- **Realtime replicability is capped** by the table above.
- **Estimand B stays NOT READY**, but for a corrected reason: the wall-clock
  freeze problem is real at a 15 s median, not a 28.5 s one.
- **A second, separate cost:** at these lags the successor usually *already
  exists* when we freeze, so the prospectivity check excludes the row. Latency
  does not merely delay scoring — it eats the scorable sample. This is the live
  `excluded_not_prospective` count, and it is pinned by a test.
