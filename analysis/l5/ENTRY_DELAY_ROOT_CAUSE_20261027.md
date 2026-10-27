# The entry delay is the settlement sweep, measured

Status: **root cause measured in production**, fix implemented behind a
disabled default, not deployed. Deployment needs owner authorization.

Deployment under observation: Railway `l5-pmus-live`
(`87f1834e-530e-4fc4-a310-91c8d3add196`), deployment `4fc4e25b`, commit
`caf790a5` (= `f6ad0f55` + the preflight-attempt recorder `eb332879` +
a trigger-only commit). `L5_SHADOW_ONLY` enabled, `submit` blocked.

## What we thought, and what is actually true

We had been chasing a preflight gate. Campaign 2's nine windows passed
preflight at **+80.1s to +107.8s** after the boundary while the venue is
tradable at **+0.6s**, so the working hypothesis was that some check in
`maybe_preflight` was failing attempts 1..5 and the backoff ladder
(`3, 6, 12, 24`) was eating the first ninety seconds.

That hypothesis is wrong. The first `L5_LIVE_PREFLIGHT_ATTEMPT` record
from production:

```
2026-10-27T21:32:22.794Z L5_LIVE_PREFLIGHT_ATTEMPT
{"market":"cpc-btc-updown-15m-2026-10-27-2130z","attempt":1,
 "verdict":"PASSED","t_in_window_s":48.81,"attempt_duration_s":1.574,
 "next_attempt_in_s":6.0,"first_blocking":null,"blocking_failures":null,
 "fatal":null,"book_read_attempts":["attempt1:ok"],
 "ws_book":{"snapshot":true,"bid":"0.9200","ask":"0.9300",
            "two_sided":true,"stream_age_s":0.083,"book_updates":902,
            "market_state":"MARKET_STATE_OPEN","fallback_max_age_s":30.0},
 "tick_wait_s":null}
```

**Preflight passed on attempt 1, in 1.574 seconds, with a two-sided
websocket book 83 milliseconds old, and nothing blocking.** There is no
failing gate and there never was one.

Read the two clocks against each other. `t_in_window_s` is taken from the
`now` the main loop passes into `tick`, so the tick began at
window + 48.81s = 21:30:48.8 — the process start. The record printed at
21:32:22.79 = window + 142.8s, and `emit` uses `flush=True`, so that is
the write time, not a buffer artifact. Subtract preflight's own 1.574s:

> **~92 seconds elapsed inside one tick before `maybe_preflight` was
> reached.**

## Where the 92 seconds go

Railway's `network-flow` egress trace for that interval, one host
(`104.18.38.40:443`, the venue gateway), flows per second:

```
21:31:26  1     21:31:42  6     21:31:58  4     21:32:14  4
21:31:27  3     21:31:43  2     21:31:59  2     21:32:15  2
21:31:28  4     21:31:44  4     21:32:00  4     21:32:16  4
...             ...             ...             21:32:17  3
21:31:40  4     21:31:56  4     21:32:12  5     21:32:18  4
21:31:41  2     21:31:57  3     21:32:13  2     ----------- preflight
```

52 unbroken seconds at ~3 flows/second — 1.5 HTTPS connections plus 1.5
DNS lookups, i.e. **exactly the shared limiter's `rps=1.5`**, with
`http_429` at zero throughout. This is not the venue throttling us. It is
`VenueRateLimiter.before()` sleeping synchronously on the tick thread.

The caller is `sweep_settlements`. A transitive scan of everything the
tick runs *before* `maybe_preflight` finds venue reads in exactly two
places, both the same loop:

```
roll            -> two_sided_close -> sweep_settlements
(tick, line ~5242) two_sided_settle -> sweep_settlements
```

`sweep_settlements` reads one `/v1/markets/{slug}/bbo` per market returned
by `store.markets_with_exposure()`, serially, through the shared limiter.
And the heartbeat for this deployment names **86 distinct BTC15m markets**
still carrying exposure, the oldest `cpc-btc-updown-15m-2026-10-12-0030z`
— a fortnight back.

86 reads ÷ 1.5 rps = **57.3 seconds**, which is the 52-second trace plus
the markets whose `SETTLEMENT_POLL_S` had not yet elapsed. The arithmetic
closes.

## Why 86 markets, and why it is a treadmill

Those markets cannot retire. `retire_settled_market` refuses a market
whose legs still hold unmatched inventory, and the retirement-blocker
distribution across both campaigns is dominated by exactly that:

| `retirement_blocked_by` | 0100z | campaign 2 (8 windows) |
|---|---|---|
| `LEG_HOLDS_INVENTORY:UP` | 72 | 36 |
| `ORDER_STILL_LIVE` | 13 | 14 |

So every window that ends with stranded inventory adds a market to the
sweep permanently. The cost of the sweep grows monotonically with the
number of windows we have ever traded badly.

Worse, it is self-sustaining. `SETTLEMENT_POLL_S` is **10 seconds** and
the pass takes **~55 seconds**, so by the time the sweep finishes, every
market it started with is due again. The next tick re-reads them all.
The engine is not working through a backlog; it is running on a treadmill,
and the decision path — which sits after the sweep in the tick — waits for
each lap.

`markets_with_exposure()` returns `sorted(...)`, i.e. slug order, i.e.
**chronological, oldest first**. So the window that just closed, whose
settlement is the one we are actually waiting for, is read last of 86.

## What this explains

* the +80.1s–+107.8s preflight pass times across campaign 2 (and the
  spread: it tracks how many markets were in the book that day);
* the 121 requests and 111 throttled waits counted at one window roll;
* the 61.14s of in-tick limiter sleep, which under-reported the true cost
  because `lim.tick_begin()` is called *after* the sweep, immediately
  before `maybe_preflight` — which is also why this record carries
  `tick_wait_s: null` beside 92 seconds of real delay;
* the skipped 60-second heartbeat at that boundary;
* why the engine averaged 1.98 matched contracts per window: it had
  roughly 13 of the window's 15 minutes left by the time it could act.

## The fix

One change, in `sweep_settlements`: collect what is due, then read at most
`PMUS_SETTLEMENT_SWEEP_CAP` markets per sweep, **oldest read first**.

* **Deferred, never dropped.** `last_settlement_poll` moves only for a
  market actually read, so the rest stay due and come back next tick.
* **A market never read sorts first** (`last == 0.0`), so the window that
  just closed is read on the very next tick instead of behind the
  fortnight of markets that will never retire. This is the property that
  matters, and it is why the ordering key is read time rather than slug.
* **Unset is unbounded**, in the iteration order the loop has always used,
  so the default is byte-for-byte today's behaviour. Blank, `0`,
  negative and unparseable all mean unbounded too: a cap a typo can
  introduce would silently throttle settlement detection, and settlement
  is what the realized total — and the -$5 loss halt measured on it — is
  made of.
* No gate, no risk limit, no order routing, no retirement rule, no
  settlement accounting is touched. The set of reads is unchanged; only
  how many happen in one pass.

At `cap = 4` the pre-decision sweep costs **2.7s instead of 57.3s**, and
all 86 markets are still polled, over ~22 ticks.

Tests: `tests/test_l5_settlement_sweep_cap.py`, 19 cases, no network.

## What is NOT claimed

Latency is measured. Profit is not. A sweep that returns 54 seconds to the
window does not by itself produce a single additional matched contract:
the engine must then find an executable pair in that time, and the second
confirmed pathology — a round holding one leg recomputes a constant
`pair_cost 0.9500 / edge 0.0500` against a frozen complement and refuses
it (`LEG_WOULD_CROSS` on 85 and 50 plans respectively) — is untouched by
this change. Earlier entry is a precondition for more completed pairs, not
a cause of them.

Any claim about incremental settled net P&L needs a bounded live probe
with settlement audit, and remains unauthorized.
