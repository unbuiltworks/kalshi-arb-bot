# Cache-bypass deployed — result: hypothesis FALSIFIED

## Deployment

| | |
|---|---|
| deployed SHA | **`e71c441ed0cc2a5cbd1745d7684daa8f62e79fd4`** |
| deployment | **`62e58760`**, SUCCESS 23:21:36Z, `us-east4-eqdc4a` |
| service | `l5-pmus-live` (unchanged; no new service) |
| source pin | now `claude/compassionate-allen-wic64u` @ `e71c441e` (was `claude/l5-narrow-campaign-prep` @ `c39183a3`) |

**A first attempt was SKIPPED.** Pinning the commitSha alone did not build:
the service's `watchPatterns` is `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`,
so production kept running `c39183a3` untouched until the trigger file was
appended. Worth recording — it is the same trap that cost an earlier
campaign its activation window.

### Production-bound diff, audited line by line

Five files reach the image (the Dockerfile COPYs an explicit per-file list).
Grepping every added line for `submit|arm|cancel|claim_submission|transport|
EXECUTION_MODE|MAKER_ONLY|max_total_exposure|max_live_orders|PMUS_Q1|halt`
returned **only docstrings and one read-only time parameter**.

| file | + | nature |
|---|---:|---|
| `l5_pmus_realtime_observer.py` | 42 | **the collector cache-bypass** |
| `l5_pmus_live_runner.py` | 104 | read-only: sampler first-leg cost, shadow decision recorder |
| `pmus_sports_l5_decision.py` | 31 | read-only: held-leg cost in the snapshot |
| `l5_runtime_config_verify.py` | 7 | read-only: one observed telemetry row |
| `l5_two_sided_probe.py` | 108 | the 0.60 opening ceiling — **refuse-only** |

**Deviation disclosed:** you asked for the collector change only. The
refuse-only 0.60 guard shipped with it because the verifier row above calls
`TS.max_first_leg_own_price`, so reverting the probe alone would
**AttributeError at boot**. A hand-cut partial revert would have been an
untested combination; the guard can only *decline* an opening and can never
cause an order. Flagging rather than burying it.

## 1. Health and the submission gate

Service healthy, and the gate is blocked on **three independent counts**
(`ok: false`):

| row | verdict |
|---|---|
| `owner_arm_token` | **FAIL** → `OWNER_AUTHORIZATION_ABSENT` (step 11) |
| `absolute_deadline` | **FAIL**, EXPIRED by 3,688.9 s → `AUTHORIZATION_EXPIRED` (step 26) |
| `campaign_approval` | **FAIL**, `approval_stale` (`ac855ce3…`) |
| halt_gate | PASS, permitted |
| shadow_lock | PASS, not engaged |
| residual_ceiling | PASS, 3 contracts |

`L5_DEFENSIVE_COMPLETION` off, 1.20 default not enabled, 1.00 ceiling
intact, withdrawn mandate not installed.

## 2–4. The cache-bypass result

Fresh container, 18 polls in the first ~70 s:

| counter | value |
|---|---|
| `ingest:polls_ok` | 18 |
| `ingest:rows` | 3,600 (= **exactly 200.0**/poll) |
| `ingest:dup` | **3,600 of 3,600** |
| **`ingest:new`** | **0** |
| cursor `keys` | **5,556 — unchanged** |
| `newest_row_event_utc` | **17:54:50Z — still frozen** |
| `last_admitted_utc` | **null** |
| `feed_errors` | 0 |

- **New L5 trade IDs: 0.** No new timestamps; `newest_row_event_utc` did
  not move.
- **Admission: nothing reached it.** `ingest:admitted` never incremented and
  `last_admitted_utc` is null, so attribution and staleness checks were
  never exercised — there was nothing to check.

### The hypothesis is cleanly falsified, which is a real result

The request now carries `Cache-Control: no-cache, no-store, max-age=0`,
`Pragma: no-cache`, **and a per-poll `_cb` making every URL one that has
never been requested before**. A cache cannot serve a response for a URL it
has never seen. The API still returns the identical 200-row page whose
newest event is 17:54:50Z.

**So the frozen page is what `data-api` genuinely returns for
`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`.** Caching is excluded, along
with dedup, pagination, timestamps and the `type=TRADE` filter — all
previously ruled out from code semantics.

## 5. Remaining blocker: attribution, not ingestion

Two hypotheses survive, and both are identity/venue questions:

1. **Proxy-wallet mismatch** — the profile's trading is attributed to a
   different address than the one we poll. Polymarket profiles are proxy
   contracts, the username is **never resolved** in our code, and the
   address is hard-coded in 11 places. Now the strongest candidate: you see
   trades on the profile, and that address demonstrably has none after
   17:54:50Z.
2. **Venue migration** — recent activity sits on a venue this endpoint does
   not cover. `gateway.polymarket.us/v1/activities` returned HTTP 404 on
   three unauthenticated parameter shapes, so it is not publicly readable
   and I could not test it.

Neither is resolvable from this session: `data-api.polymarket.com`,
`polymarket.com` and `gamma-api.polymarket.com` are all refused from this
container, and a Polygon RPC for the on-chain path
(`src/l5_chain_observer`) would be too. **No existing running service polls
a second address**, and creating one is out of scope.

### The one read that settles it

```
curl -s "https://data-api.polymarket.com/activity?user=0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68&limit=5&type=TRADE" | head -c 400
```

- rows newer than 17:54:50Z → our collector is still at fault, re-open it
- nothing newer → **the address is not where the trading is**, and the fix
  is the address or the venue, not the collector

Then, from the profile page, the `0x…` address `@l5zn1bwom8etsk` actually
trades under. If it differs from the hard-coded one, that single value is
the whole fix.

## 6. Not done

No live trading, no defensive crossing, no mandate installed, no new
service, no limit changed. The engine remains disarmed on three counts.
