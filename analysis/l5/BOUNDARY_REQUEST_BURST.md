# The window-boundary request burst: what is proven, what is not

Read-only investigation of the 84–142s the engine loses at every BTC15m
window roll. Production unchanged; the one code change here is off by
default in two independent ways.

## The bottleneck, exactly

**`RateLimiter.before()` sleeps on the money-path tick thread, and the tick
runs the strategy last.**

`Runner.tick` is one sequential pass: `maybe_preflight` → `poll_orders` →
`resolve_pending_terminal` → `sweep_settlements` → `reconcile_if_needed` →
`poll` (the strategy) → `shadow_tick`. Every venue read in the first five
is paced at `rps=1.5` by a shared token bucket whose `before()` calls
`self._sleep(wait)` inline. So the strategy does not run until that tick's
housekeeping reads have each been metered out, one every two-thirds of a
second.

Three independent measurements of the same pacing:

| evidence | 1515z roll | 0015z roll |
|---|---|---|
| limiter `requests` | +121 | +128 |
| limiter `throttled_waits` | +111 | +111 |
| limiter `total_wait_s` | **+61.14s** | **+60.15s** |
| `http_429` / `in_cooloff` | 0 / false | 0 / false |
| 60s heartbeat | one beat skipped | one beat skipped |

And the egress trace, which settles it. Railway `network-flow`, egress
to :443, per second across the 1515z stall:

```
15:15:06  #####  5      15:15:22  ###  3      15:15:42  ###  3
15:15:07  ##     2      15:15:23  #    1      15:15:43  #    1
15:15:08  ##     2      15:15:24  ##   2      15:15:44  ##   2
   ... 1,2,2,1,2,1,3,1,2,1,2,2,2,1,2,1 repeating, period 10s ...
15:16:04  ##     2
```

**Exactly 15 flows per 10 seconds, for 58 consecutive seconds: 1.5/s, the
token-bucket rate, to four significant figures.** Then the loop resumes and
fires 7/10/21/4 flows in four seconds. DNS over the same window:
`api.polymarket.us` 215, `gateway.polymarket.us` 72.

## 1. What generated the 121 requests — NOT ANSWERABLE

**There is no per-path or per-purpose request counter anywhere in this
process.** `RateLimiter.counts` totals `requests`, `demanded`,
`throttled_waits`, `http_429`, `refused_in_cooloff` and `recoveries`, and
nothing else. The heartbeat republishes those totals. So the burst is
measurable three ways and attributable none.

What I can bound from the code, by static enumeration of every
limiter-paced read:

| purpose | endpoint | multiplicity | throttle |
|---|---|---|---|
| `account` | `GET /v1/account/balances` | 1 per preflight | preflight backoff 3/6/12/24s |
| `reconcile` | `GET /v1/portfolio/positions`, `GET /v1/orders/open` | 2 per preflight, 2 per reconcile | `RECONCILE_RETRY_S` |
| `book` | `GET /v1/markets/{slug}/book` | 1 per preflight | — |
| `settlement` | `GET /v1/markets/{slug}/bbo` | 1 per market | `SETTLEMENT_POLL_S` 10s |
| `order_progress` | `GET /v1/order/{id}` | 1 per live order | `ORDER_POLL_S` 6s, aggregate `ORDER_POLL_RATE` 0.5/s |
| `order_preview` / `order_submit` | `POST /v1/order/preview`, `POST /v1/orders` | per submission | writes never wait |

Measured against a counting fake, **one `preflight()` issues 3 paced
reads** — so preflight is not the bulk. `order_progress` at its 0.5/s cap
could account for ~64 of 129 seconds' worth, and `resolve_market` tries up
to four host/path combinations (and bypasses the limiter entirely, via
`http.request` rather than `_request`, so it is in neither counter). The
roughly even `api`/`gateway` destination split is consistent with
`resolve_market` and with `verify_alignment`'s pagination, neither of which
is counted.

**I am not going to name a dominant caller from this. The honest finding is
that the instrumentation to name one does not exist, and that is the first
thing to fix.**

## 2. Redundant / avoidable / cacheable — candidates, unquantified

- `resolve_market` tries `/v1/market/slug/{slug}` first on each of two
  hosts, and its own docstring records that this path **404s for every
  BTC15m window**. Two knowingly-failing requests per resolution; the
  working `(host, path)` pair is cacheable per series.
- `verify_alignment` pages `/v1/events?series_id=375` from offset 0 in
  steps of 100, up to 4000, once per window. Measured end-to-end **8.21s**
  today. The target window is at the tail; a reverse or seeked walk would
  be a handful of requests.
- Preflight re-runs whole from scratch each window, repeating `positions`
  and `orders/open` that `reconcile_if_needed` also reads.

Each is plausible and none is sized, because §1.

## 3. Did the 61.14s delay eligible orders? — YES, but by ORDERING

Writes already never wait (`before(write=True)` skips the token queue — "a
bounded maker order must not sit in a queue behind a settlement poll"). So
the delay is not contention on the order itself. It is that
`L5_LIVE_ARMED` fires at the **end** of the burst in all 14 campaign
transitions, 0.0–5.3s before the first order, because arming runs inside
`maybe_preflight` at the top of the same sequential tick.

## 4. Earlier executable orders from recorded books? — NOT ANSWERABLE

The executor's authenticated websocket mirror is the only authenticated
book we have, and it recorded nothing during the stall — the process was
asleep. The public tape covers some boundary intervals (1430z: bid
0.58→0.66 and ≥12,332 shares traded before our first order) and not others
(1515z: reslate at +39.7s then a 404). Public book, no fills, displayed
depth only. **Saved latency is not demonstrated profit and nothing here
demonstrates it.**

## 5. Can requests be cut without weakening anything? — PARTLY, AND THE
## LIMIT IS NOT THE LIMITER

Inspecting what each caller does with a failed read:

| caller | on exception | safely deferrable? |
|---|---|---|
| `sweep_settlements` | counts `settlement_read_error`, market stays unretired — its docstring calls this "the safe direction" | **yes** |
| `poll_orders` | `mark_uncertain(...)` → submissions refused | **no** |
| `reconcile_if_needed` | `mark_uncertain(...)` → submissions refused | **no** |

So **deferring is safe for exactly one purpose today**. Everything else in
the tick treats an unread as a reason to fail closed harder, which is
correct and must not change. This is the key structural result: the path to
a faster boundary is *fewer requests demanded* (§2) or *housekeeping off
the money-path tick*, not smarter scheduling of the same requests.

## The change implemented

`src/l5_pmus_transport.py`, `RateLimiter`:

1. **Per-purpose accounting — always on, pure observation.** `before()`
   takes a `purpose`; `metrics()["by_purpose"]` reports demanded /
   requests / waits / wait_s / deferred / refused_in_cooloff per purpose.
   The purpose is derived from the ROUTE by `purpose_for(method, path)`
   inside `_request`, the single door every venue request goes through.
   Nothing about what leaves the process changes.

   **This is the second shape of the change, and parity is why.** Tagging
   each call site with `purpose=` broke five tests:
   `test_the_read_is_a_plain_get_on_the_balances_route` reads `account()`'s
   source and requires the literal `_get("/v1/account/balances")`, and four
   `test_l5_binary_settlement` cases stub `_get` with a fake taking no
   extra keyword. Those tests were right — a money-path call site that has
   to grow an argument for accounting is accounting leaking into the money
   path. Classifying by route keeps every call site byte-identical, puts
   the mapping in one readable table, and is strictly smaller. One test
   fake did still need widening: the cancel test stubs `limiter.before`
   itself with a two-parameter lambda, and `before`'s signature is what
   this change alters.
2. **A per-tick wait budget — off by default, twice.**
   `PMUS_TICK_WAIT_BUDGET_S` (default `0`, which disables it) and
   `PMUS_DEFERRABLE_PURPOSES` (default empty, which makes any budget a
   no-op). When both are set, a tick that has already slept the budget
   refuses further requests **whose purpose is on the allowlist** by
   raising `VenueThrottled` — the same exception the cool-off already
   raises, so every caller's existing, already-proven handling applies and
   nothing new has to be established about it. `Runner.tick` calls
   `limiter.tick_begin()` at the top of each pass.

`order_progress` and `reconcile` may never be allowlisted, and the tests
pin that the budget does not touch them.

### Measured latency reduction — mechanism only

Fake clock, 120 queued reads on one tick at rps=1.5, burst=4. **This is the
limiter in isolation. It is not a production measurement and it is not
profit.**

| budget | tick sleeps | served | deferred |
|---|---:|---:|---:|
| none (today) | **77.33s** | 120 | 0 |
| 2.0s | 2.00s | 7 | 113 |
| 4.0s | 4.00s | 10 | 110 |
| 8.0s | 8.00s | 16 | 104 |
| 15.0s | 14.67s | 26 | 94 |

And the part that matters: the budget can only defer the allowlisted share
`f` of a real burst, and `f` is unknown (§1).

| if settlement reads are | tick sleeps | saved vs 77.33s |
|---|---:|---:|
| 10% of the burst | 69.33s | 8.00s |
| 25% | 57.33s | 20.00s |
| 50% | 37.33s | 40.00s |
| 100% | 4.00s | 73.33s |

**At the plausible end — settlement is one read per market per 10s — `f` is
small and the saving is seconds, not a round.** The sensitivity table is
why the accounting has to land before the budget is worth enabling.

### Hypothetical incremental settled P&L — reported separately and not claimed

If a boundary fix returned ~95s of a 900s window, and rounds take ~99s
(1500z: first order +142.4s, round 4 by +538s), that is about one extra
round ≈ 1.84 matched contracts ≈ **+$0.098/window** at the measured
$0.05343 net per matched contract. **That figure assumes the 95s is
recoverable, and this change does not recover it.** It is the prize for
solving §2, not a result of what is implemented here.

## GO/NO-GO

- **Per-purpose accounting: GO for shadow validation.** Zero behaviour
  change, parity identical, and it is the only way to answer §1.
- **The tick wait budget: NO-GO for shadow validation.** Its benefit is
  bounded by an unknown `f`, and on the one purpose that is safely
  deferrable today `f` is probably small. Ship the accounting, read
  `by_purpose` off one campaign's heartbeats, then decide.
- **Nothing here is a reason to change production, risk limits or
  authorization.**
