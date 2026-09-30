# Option A tested and RULED OUT: the public gateway is a ~30s cache

Directive tested: *"Stand up an external forward recorder outside Railway
using the existing public full-L2 feed and the safest available trade-print
source."* I built it, measured it, and it cannot do the job. Reporting that
rather than producing a table built on a cache TTL.

## 1. There is no public print source at all

Probed the gateway directly (read-only, unauthenticated):

| endpoint | result |
|---|---|
| `/v1/markets/{slug}/trades` | 404 |
| `…/history`, `…/prints`, `…/fills`, `…/ticks`, `…/activity` | 404 |
| `/v1/markets/{slug}/book` | **200** — full ladder |
| `/v1/markets/{slug}/bbo` | **200** — `lastTradePx` + cumulative `sharesTraded` |
| `openapi.json`, `swagger.json` | 404 (no discoverable catalogue) |
| WS upgrade to `api.polymarket.us/v1/ws/markets` | **401** `valid API key authentication required` |

The print stream is behind the API key. So an external recorder is
print-accurate only if it carries a PMUS credential; without one there is no
"safest available trade-print source" to fall back to — there is none.

## 2. Worse: the public BOOK is cached, so it cannot even measure the mechanism

I expected the public feed to at least support the fill-free tests
(opportunity density, level persistence, oscillation). It does not.

**Recorded window 1500z:** 1,766 polls over 899.4s produced **31 distinct
ladder states** — 2.07/min. The `transactTime` field takes only 31 distinct
values, 60 identical rows each. The "median touch dwell of 30.09s" I first
computed from it is the **cache TTL**, not market behaviour.

**Confirmed live, independently:** polling slug `…1615z` every 4s, the ladder
hash changed **once in 32s**.

Scale of the shortfall against the authenticated WS on the same market:

| feed | distinct book states | per minute | ratio |
|---|---|---|---|
| authenticated WS (1330z slice) | 2,955 in 5.01 min | ~590 | — |
| public gateway (1500z window) | 31 in 15.0 min | ~2.07 | **~285× coarser** |

A queue position is earned in fractions of a second against individual
prints. A feed that refreshes twice a minute cannot see a requote, a level's
survival, or an oscillation. This is not "weaker fill fidelity" — it is the
wrong instrument for the whole hypothesis.

## 3. The fidelity gate is now code, not a caveat

`src/l5_level_persistence.py` computes `feed_resolution()` from **distinct
ladder contents**, never row count, so polling faster than the cache cannot
flatter it. It raises `FeedTooCoarse` below 30 distinct states/min and, when
a caller explicitly opts out, stamps every statistic
`CACHE_LIMITED__NOT_MARKET_BEHAVIOUR`. Pointed at the real 1500z window:

```
{'rows': 1766, 'distinct_states': 31, 'distinct_states_per_min': 2.07,
 'sufficient': False}
REFUSED: feed carried 2.1 distinct book states/min (need >= 30);
         dwell times from it would be the cache TTL, not market behaviour
```

The analyser's real statistics (control requotes = touch changes, touch
dwell, **touch returns** = levels the control abandoned and had to rejoin at
the back of the queue, level survival) are implemented and unit-tested
against synthetic ladders. They are correct and they are waiting for a feed
that can feed them.

## 4. Consequence for the five options

* **Public-only forward cohort** — ruled out by measurement, for fills *and*
  for the mechanism. Not a weaker version of the test; not the test.
* **External recorder outside Railway** — viable, but only with a PMUS API
  key on that host. The venue, not Railway, is what gates print access.
* **Recorder writes future raw data to S3** / **read-only exporter service**
  — both still need one controlled change or one more deployment, and both
  keep working afterwards.
* **GitHub Actions as transfer runner** — cannot read a Railway volume
  without the same file-access path that is currently quota-blocked.
* **One minimal Railway export, then never again for research data** — the
  only path that needs no venue credential moved anywhere new.

I did not touch `pmus-ws-evidence-recorder`: no restart, no redeploy, no
variable, no branch, no config. I did not read or move any credential.
