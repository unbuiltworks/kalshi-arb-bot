# Disarmed production verification — 85dbaf1

**SHA** `85dbaf13248aebced949518b8b41523c6c0c1e8b` · deployment `668904a1`
SUCCESS 2026-09-26T23:32:07Z · service `l5-pmus-live` · **DISARMED throughout.**

Economic baseline unchanged: **$61.42595** balance, **−$0.27170** cumulative
account cash P&L from the $61.69765 programme start.

---

## PROVEN LIVE DISARMED

### 1. SHA and disarmed state
`commitHash 85dbaf13248aebced949518b8b41523c6c0c1e8b`. Every heartbeat:
`armed false`, `owner_arm_token false`, `preflight PASSED`, `halt false`.
Limits unchanged — `max_usd_per_market 5`, `max_abs_residual 10`,
`max_total_exposure_usd 5`, `max_concurrent_markets 1`, `max_live_orders 8`,
`daily_realized_loss_halt_usd 5`, λ `0.04518752824220514525`,
`frozen_rule 030d8bf`.

### 2. Accounting authoritative and reconciled
Read-only recovery, deployment `f3b388ad`, 23:32:31Z, fingerprint
`d51b44a3fa83`:

```
/v1/orders/open          {"orders": []}
/v1/portfolio/positions  {"positions": {}, "availablePositions": []}
currentBalance           61.42595      unsettledFunds 0   openOrders 0
```

```
61.42595 - 61.69765 = -0.27170 = realized_total
  attributed    +2.35460
  unattributed  -2.62630
```

`realized_total -0.27170` on **every** heartbeat and **every** reconcile
across the whole window. No mutation.

### 3. The process-wide limiter survives a real rollover

**THE HEADLINE.** Across the 23:45 BTC15m rollover, `roll()` replaced the
transport (market slug `2330z` → `2345z`) and the limiter state carried:

| | 23:44:21Z `2330z` | 23:46:24Z `2345z` |
|---|---|---|
| `requests` | 10 | **22** |
| `demanded` | 10 | **22** |
| `uptime_s` | 736.0 | **859.0** |
| `throttled_waits` | 2 | 2 |
| `max_wait_s` | 0.642 | 0.642 |
| `total_wait_s` | 1.09 | 1.09 |

Monotonic. Compare the defect this replaced, from the pre-fix soak:

```
22:57:06Z  2245z transport   requests 134
23:00:40Z  2300z transport   requests  24    <- reset
```

The three wait fields being *byte-identical* across the boundary is the
stronger proof: this is the same object, not a new one that happened to
count similarly. A 429 taken at 23:44:59 would now still be in cool-off at
23:45:01.

The reconcile series shows the same thing independently:

```
23:32:06Z  2330z  requests  6   uptime    1.4
23:37:09Z  2330z  requests  8   uptime  300.5
23:42:07Z  2330z  requests 10   uptime  601.2
23:47:14Z  2345z  requests 24   uptime  901.8   <- across the roll
```

### 4. Preflight caching/backoff behaviour
`preflight_attempts` 1 → 5 across the rollover: four attempts at the
3/6/12/24s schedule. Total rollover cost **12 requests over 123 s =
0.098 req/s**, against the **0.63 req/s** measured on the 2300z rollover
before the fix — a 6.4× reduction.

No cache: every preflight read is made on every attempt (see
`85dbaf1` — the account read carries the balance that
`account_binding_proven` and `identity_probe.buying_power_usd` are computed
from, so caching it would assert a stale balance). The cost is bounded by
asking less often, not by remembering answers.

### 5. Periodic authoritative reconciliation
Four `trigger: SOAK_PERIODIC`, `armed: false` reconciliations at 23:32:06,
23:37:09, 23:42:07, 23:47:14. Every one:

```
drift {}   gross_detail_lost []   gross_net_agrees {}
live_orders_pending_terminal []   live_orders_released_unacked []
pending_terminal_returned []      pending_terminal_total 0
uncertainty_cleared true          authority VENUE
```

Exactly **2 requests each** (6→8→10), which is the positions + open-orders
pair and nothing else.

### 6. Telemetry
```
http_429          0          consecutive_429 0     in_cooloff false
unmet_demand      0          demanded 22 = requests 22
max_wait_s        0.642      mean_wait_s 0.544     total_wait_s 1.09
oldest_unread_s   0.0        overdue_s 0.0         live_orders 0
transmitted_rps   0.0256     demand_rps 0.0256
```
`demanded == requests` with `unmet_demand 0` — nothing is backing up.
`throttled_waits 2 / total_wait_s 1.09` was the boot burst being paced, so
the limiter is demonstrably *in* the request path, not merely constructed.

### 7. Nothing bad happened
A single filtered query for `L5_LIVE_ERROR`, `IncoherentBook`,
`SETTLE_REFUSED`, `L5_LIVE_SETTLED`, `RECONCILE_FAILED`, `ORDERS_STALLED`,
`PENDING_UNRESOLVABLE`, `ACCOUNTING_REBASED`, `KEYS_CANONICALIZED`,
`FLAT_ACCOUNT_ADOPTED`, `ORDER_SIDE_SELL` over the whole deployment matched
**only reconcile events** (whose bodies contain the string
`gross_detail_lost`). `retired_markets` stayed `["...2115z"]` with zero
settlement events and zero settlement reads. No legacy side keys. The
migration and rebase did not re-fire — third restart in a row that repeats
nothing.

### 8. Collector untouched
`git diff --name-only 85dbaf1~2 85dbaf1` contains none of
`deploy/l5_window_collector/**`, `src/l5_window_collector.py`,
`scripts/l5_window_collector_run.py`. Four files changed: two source, two
test.

### Full suite
42 failures at the `12a30e2` baseline, 42 now, **empty diff in both
directions** — zero new, zero silently fixed.

---

## PROVEN ONLY BY REGRESSION TEST

These are correct in `tests/test_l5_request_schedule.py`,
`test_l5_costless_inventory.py` and `test_l5_continuous_lifecycle.py`
against a fake venue. A fake venue is not the venue.

- **429 → fail-closed → clears only on an authoritative answer.** No real
  429 occurred in any disarmed run (`http_429 0` throughout). The cool-off,
  the doubling backoff, `VenueThrottled` raising without sending, and
  `time_to_authoritative_recovery_s` are all untested against the real
  venue. I did not manufacture one: the collector and recorder share this
  account's quota.
- **Serial parked-order drain** (8 parked → 8 reads, not 8 per interval)
  and **submissions blocked for the entire drain**.
- **Aggregate order-poll cap** at 8 live orders (≤0.5 req/s).
- **The whole stressed demand** staying under the ceiling.
- **Adoption of a venue order**, canonical side from the raw word, and the
  full 2045z/2100z/2115z reconstructions.
- **Restart mid-flight** resuming a parked order from the file.

## STILL REQUIRES ARMED VENUE EVIDENCE

Nothing below can be obtained disarmed, because it all requires an order to
exist at the venue.

1. A real resting maker order: ACK, venue order id, `LIVE_AT_VENUE`.
2. Order-progress polling **under load** — the measured 0.027 req/s is
   ~1/26th of the armed full-envelope figure. `oldest_unread_s` and
   `overdue_s` have only ever been read with `live_orders 0`.
3. A real partial fill, then its completion, booked idempotently on the
   cumulative.
4. A real disappearance from `/v1/orders/open` → parking → authoritative
   `/v1/order/{id}` resolution. This is the exact 2045z defect and it has
   never once run against the real venue.
5. Coherent quantity **and** cost surviving to settlement.
6. Exactly-once retirement of a market that actually held inventory.
7. Authoritative cash reconciliation closing on a non-zero delta.
8. Whether `PMUS_RPS = 1.5` is genuinely under the venue's limit. We saw
   429s somewhere in the 1–3.5 req/s range on the 26th; nothing since has
   narrowed it.

---

## Request budgets, as shipped

| scenario | before | now | % of 1.5 ceiling |
|---|---|---|---|
| normal (1–2 live, certain) | 0.533 | **0.533** | 36% |
| full envelope (8 live, certain) | 1.533 | **0.700** | 47% |
| stressed (8 live + 8 parked + uncertain + rollover) | 4.933 | **1.250** | 83% |
| rollover preflight (measured) | 0.630 | **0.098** | 7% |

`PMUS_RPS` remains 1.5 and is treated as a ceiling.
