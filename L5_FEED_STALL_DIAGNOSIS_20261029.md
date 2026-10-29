# L5 ingestion stall — diagnosis, 2026-10-29

Production **disarmed** throughout (`PMUS_Q1_LIVE=NO`, preflight
`fail: ["owner_arm_token"]`). No collector change was made — see
"Why I did not ship a fix".

## I accept the owner's evidence and have changed my conclusion

The owner can see L5 trading on `polymarket.com/@l5zn1bwom8etsk`. My earlier
report said L5 inactivity and an upstream freeze were indistinguishable from
here. With the profile as evidence, that is resolved: **this is a defect on
our side of the boundary, not L5 inactivity.** My previous framing was wrong
and this supersedes it.

## The stall, measured on the deployed collector

`L5_LIVE_HB`, deployment `8149d173`, read at 21:14:35Z:

| field | value |
|---|---|
| `newest_row_event_utc` | **2026-10-29T17:54:50Z** — frozen **3h 20m** |
| `last_row_utc` | 2026-10-29T21:14:03Z — advancing |
| `ingest:polls_ok` | **1,473** |
| `ingest:rows` | 294,600 |
| `ingest:dup` | **294,600** (100%) |
| `ingest:new` | **0** |
| `ingest:admitted` | absent / `last_admitted_utc: null` |
| cursor `keys` | **5,556** — unchanged across all 1,473 polls |
| `feed_errors` | **0** |
| `rows_btc15m` | 279,870 and growing |

294,600 / 1,473 = exactly **200.0** rows per poll. For 3h20m the endpoint
returned a byte-identical 200-row page, every row already known.

## What the evidence RULES OUT

- **Dedupe / `action_key` collision.** The key is
  `transactionHash|asset|side|price|size|timestamp` — collision is
  implausible. Decisively, `newest_row_event_t` is computed over **every row
  before** the `cursor.known()` check (`l5_pmus_live_runner.py:2478-2488`),
  so no dedupe fault can freeze it. The static `keys` count corroborates.
- **Pagination.** `l5_activity` passes **no offset and no cursor** — there is
  no paging to get wrong. And the feed is newest-first, read from the head,
  so paging could only ever hide *older* rows, never newer ones.
- **Our slug filter.** `rows_btc15m` is growing and no `ingest:refused:*`
  counter ever incremented, so nothing was filtered out — the rows arriving
  are btc15m, just the same old ones re-served.
- **The staleness bound.** `max_action_age_s` 900 never fired; nothing
  reached it.
- **The `type=TRADE` filter.** `l5_identity_strategy_audit` censuses 784,500
  activity items for this wallet: TRADE 768,325, REDEEM 15,971,
  MAKER_REBATE 114, TAKER_REBATE 73, REWARD 20, MERGE 0. The filter is
  correct for this wallet.
- **A short-TTL edge cache.** 3h20m of byte-identical responses is not a
  normal cache TTL.
- **Wrong base wallet identity (weakened, not eliminated).** The same
  address `0xb945...db68` underpins all 784,500 historical items and the
  entire canonical tape attributed to `@l5zn1bwom8etsk`, so the base mapping
  has been right. Nothing in the codebase resolves the *username* to an
  address — it is hard-coded in 11 places — so a *newly added* proxy would
  be invisible to us.

## Ranked remaining hypotheses — NOT discriminated

1. **L5 now trades from an additional/new proxy wallet.** Polymarket
   profiles are proxy contracts. This fits every observation exactly: a
   wallet with >=200 lifetime trades that stops produces a *permanently*
   immutable newest-200 page, with `feed_errors 0` and honest 200-row
   answers forever.
2. **L5's recent BTC15m trading moved venue** (e.g. to Polymarket US), so
   the global `data-api` for that address legitimately has nothing newer
   while the profile aggregates both. I probed
   `gateway.polymarket.us/v1/activities` unauthenticated and got HTTP 404
   (`code 5`) on three parameter shapes, so it is not publicly readable and
   I could not test this.
3. **A server-side stale materialized view** on `data-api` for this query.

## Why I could not finish, and exactly what unblocks it

The decisive comparison needs one HTTP read, and this session cannot make
it. Both routes are blocked by the environment's network policy:

- container egress: `data-api.polymarket.com`, `polymarket.com`,
  `gamma-api.polymarket.com` → CONNECT refused / `http=000`
  (`connect_rejected`). `gateway.polymarket.us` is allowed.
- the fetch path: all four URLs failed at DNS (`getaddrinfo ENOTFOUND`).

To unblock, add under Network access → Allowed domains:
`data-api.polymarket.com`, and ideally `polymarket.com` and
`gamma-api.polymarket.com`.

### The one command that settles it

```
curl -s "https://data-api.polymarket.com/activity?user=0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68&limit=5&type=TRADE" \
  | python3 -c 'import json,sys,time; rows=json.load(sys.stdin); print(len(rows)); [print(time.strftime("%H:%M:%SZ",time.gmtime(r["timestamp"])), r.get("slug"), r.get("side"), r.get("price"), r.get("size")) for r in rows[:5]]'
```

- Any timestamp **after 17:54:50Z** → the endpoint is fine and our collector
  or its request is at fault (hypothesis 3, or a request-shape defect).
- Nothing after 17:54:50Z → the address is not where the trading is
  (hypothesis 1 or 2), and the fix is an identity/venue change, not an
  ingest change.

## Why I did not ship a fix

The user asked for a fix using existing infrastructure. I could not identify
the exact discrepancy, and the two live hypotheses demand **opposite**
changes: hypothesis 1/2 means changing *which identity or venue* we poll,
while hypothesis 3 means changing *how we request* from the same one.
Guessing would mean editing a money-path collector on an unfalsified theory.
Leaving it frozen and disarmed is the safer state, and the collector is
read-only and harmless while stalled.

## Pre-committed recovery criteria — new unique trades, not HTTP 200s

Recovery is **only** accepted on all four, from `L5_LIVE_HB`:

1. cursor `keys` **> 5,556** (a genuinely new action key admitted)
2. `ingest:new` **> 0**
3. `ingest:admitted` present / `last_admitted_utc` non-null
4. `newest_row_event_utc` **> 2026-10-29T17:54:50Z**

`ingest:polls_ok` rising is explicitly **not** evidence — it has risen 1,473
times during the stall.
