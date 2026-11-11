# Dual-cohort acceptance — gemini-maker-shadow, 2026-09-19

One Railway process, both crypto habitats, 24/7. No new service was created
and `gemini-5m-collector` was not touched.

Service `gemini-maker-shadow` (`a15785aa-…`), deployment `647912d2-…`,
`restartPolicyType: ALWAYS`, volume at `/state`.

```
19:12:04  DUAL_COHORT_SHADOW cohorts=['btc5m','btc15m'] policy=GEMINI_RECYCLE state_root=/state
19:12:04  COHORT BTC5m  prefix=BTC05M grid=5m  band=(240.0,280.0) evidence=/state/gemini-btc5m/continuous_v1
19:12:04  COHORT BTC15m prefix=BTC15M grid=15m band=(240.0,280.0) evidence=/state/gemini-btc15m/continuous_v1
```

## Both cohorts survived automatic rollover, concurrently

| time | cohort | contract | cycles | books | rested | first fills | residual |
|---|---|---|---|---|---|---|---|
| 19:19:35 | BTC5m | `…1920-UP` | 28 | 27/28 | 25 | **1** | −0.01 |
| 19:24:37 | BTC5m | `…1925-UP` | 30 | 25/30 | 18 | 0 | 0 |
| 19:29:31 | BTC5m | `…1930-UP` | 29 | **29/29** | 11 | **1** | −0.01 |
| 19:29:35 | BTC15m | `…1930-UP` | 30 | 29/30 | 36 | 0 | 0 |
| 19:34:35 | BTC5m | `…1935-UP` | 29 | 28/29 | 30 | 0 | 0 |
| 19:39:37 | BTC5m | `…1940-UP` | 30 | 27/30 | 28 | **1** | **+0.01** |
| 19:44:34 | BTC15m | `…1945-UP` | 30 | 21/30 | 13 | 0 | 0 |
| 19:44:37 | BTC5m | `…1945-UP` | 30 | 28/30 | 21 | **1** | −0.01 |

**BTC5m rolled five times** (1920 → 1925 → 1930 → 1935 → 1940 → 1945) and
**BTC15m rolled once** (1930 → 1945), with no manual intervention. The two
interleave rather than alternate: BTC15m's `…1945-UP` closed at 19:44:34 and
BTC5m's at 19:44:37, three seconds apart, and both cohorts emit heartbeats in
the same second.

Throughout: `errors: 0`, `restarts: 0`, `last_error: null`, `mutations_sent:
0` on every heartbeat of both families.

## The standing prospective result

Nine hypothetical first fills across both cohorts and both hosts, and **not
one complement behind any of them**:

| # | contract | leg | residual |
|---|---|---|---|
| 1 | `GEMI-BTC05M2609191605-UP` | YES | +0.01 |
| 2 | `GEMI-BTC05M2609191610-UP` | NO | −0.01 |
| 3 | `GEMI-BTC05M2609191620-UP` | NO | −0.01 |
| 4 | `GEMI-BTC15M2609191645-UP` | NO | −0.01 |
| 5 | `GEMI-BTC15M2609191900-UP` | NO | −0.01 |
| 6 | `GEMI-BTC05M2609191920-UP` | NO | −0.01 |
| 7 | `GEMI-BTC05M2609191930-UP` | NO | −0.01 |
| 8 | `GEMI-BTC05M2609191940-UP` | YES | +0.01 |
| 9 | `GEMI-BTC05M2609191945-UP` | NO | −0.01 |

**9 first fills, 0 COMPLETE fills, 0 matched pairs.**

One of them matters more than the others. `…1930-UP` had `books=29/29` — a
perfectly readable book on every cycle, no crossing at all — rested eleven
rungs, took its first fill, and still never completed. **The failure is not
only unreadable books.** Crossing suppresses rungs; it is not what is
stopping the second leg.

Nine is not yet the "dozens" at which the sample decides the question, but
the direction is consistent and the mechanism is now observable live rather
than inferred.

## What is not claimed

HYPOTHETICAL throughout. No order was placed, no session exists, and
`first_fills`, `COMPLETE_fills` and `matched_pairs` count rungs that were
never submitted. Nothing here is filled, settled, realized or a cost basis.
