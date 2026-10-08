# PMUS BTC15m availability — diagnosis, 2026-10-08T11:23:58Z

**Verdict: TEMPORARY MARKET-CREATION GAP, plus every BTC15m market currently
halted. NOT changed identifiers, NOT a discontinued series, NOT a discovery
failure, and NOT a defect in our selector or resolver.**

Probe `scripts/l5_pmus_btc15m_availability.py`, deployment on SHA
`07e31d32`, `ReadOnlyTransport` only.

## 1. The known-good window still resolves — on all five native routes

`cpc-btc-updown-15m-2026-10-08-0100z`, the window E1 authorized at 01:01:23Z:

| route | response |
|---|---|
| `/v1/markets/{slug}/bbo` (gateway) | `OK: marketData` |
| `/v1/markets/{slug}/bbo` (api) | `OK: marketData` |
| `/v1/markets/{slug}/book` (gateway) | `OK: marketData` |
| `/v1/market/slug/{slug}` | `OK: market` |
| `/v1/events/slug/{event}` | `OK: event` |

So the slug builder, the event-slug derivation and `resolve_market` are all
working. **This is what rules out a changed identity and a discovery failure.**

## 2. The series exists, under exactly the identifier we already use

`/v1/series` paged: **543 series**, matched on title/slug TEXT rather than on
the remembered id:

| id | slug | title |
|---|---|---|
| **375** | **`btc-updown-15m`** | **Bitcoin Up or Down (15m)** |
| 378 | `btc-updown-1h` | Bitcoin Up or Down (1h) |
| 387 | `btc-updown-5m` | Bitcoin Up or Down (5m) |
| 388 | `btc-updown-4h` | Bitcoin Up or Down (4h) |
| 434 | `btc-updown-1d` | Bitcoin Up or Down (1d) |

Series 375 carries **1,522 events**, oldest `btc-updown-15m-2026-09-22-1515z`.

## 3. The canonical identity is unchanged

Newest markets in series 375, paged to the end:

| event slug | market slug | market id | status | tick | minQty | outcomes |
|---|---|---|---|---|---|---|
| `btc-updown-15m-2026-10-08-2115z` | `cpc-btc-updown-15m-2026-10-08-2115z` | 1290960 | `MARKET_STATUS_HALTED` | 0.01 | 0.01 | `["Yes","No"]` |
| `...-2130z` | `cpc-btc-updown-15m-2026-10-08-2130z` | 1290947 | `MARKET_STATUS_HALTED` | 0.01 | 0.01 | `["Yes","No"]` |
| `...-2145z` | `cpc-btc-updown-15m-2026-10-08-2145z` | 1291501 | `MARKET_STATUS_HALTED` | 0.01 | 0.01 | `["Yes","No"]` |
| `...-2200z` | `cpc-btc-updown-15m-2026-10-08-2200z` | 1291527 | `MARKET_STATUS_HALTED` | 0.01 | 0.01 | `["Yes","No"]` |

`cpc-btc-updown-15m-YYYY-MM-DD-HHMMz`, one market per event, two outcomes,
$0.01 tick, 0.01 minimum. **Identical to what `pmus_identity.slug_for`
builds.** There is no equivalent market under a different identifier, so
nothing in the selector needs rewriting — and nothing was rewritten.

## 4. Why the 404s: a creation gap, not an absence

Markets exist for **21:15Z–22:00Z today** but NOT for the windows probed at
11:03Z (`1030z`, `1045z`, `1100z`, `1115z`, `1130z` — all 25 route/window
combinations 404, including `1100z` while it was live).

The counting supports a gap rather than a stop: 1,522 events span
2026-09-22T15:15Z to 2026-10-08T22:00Z, which is roughly 1,563 fifteen-minute
slots. About **40 windows are missing across 16 days**, and this morning's are
among them. Events are returned in creation order, not time order
(ids 148827, 148828, 148891, 148892 carry slugs 2130z, 2115z, 2145z, 2200z),
which is consistent with windows being minted in batches that can skip.

## 5. Nothing is currently tradeable anyway

Every BTC15m market the venue returned reads `MARKET_STATUS_HALTED` with
`closed: true`, including windows ten hours in the future, and
`/v1/markets?active=true&closed=false` returns 20 markets with **zero** BTC
hits (its sample is all esports/sports).

**One honest caveat.** `/v1/events?live=true&active=true&closed=false`
returned `n: 0` venue-wide, not merely zero BTC. A venue with 543 series
having no live events at all is more likely a query-shape artifact than a
fact — this repository's other callers pass `&categories=...` with that
route. So that single reading is NOT load-bearing here; the halted statuses
and the empty active-markets scan are.

Whether `closed: true` on a future window is "halted forever" or simply the
venue's pre-open representation is not established by this probe, and is not
claimed.

## Consequence for E1

No code change helps and none was made. The cohort refused correctly:
`SELECTOR_REFUSED` fires before `record_window_opened`, so **no window was
consumed**. Session `E1-COHORT-20261008T005816Z-051888d8` stands at **1/12
used, 11 remaining**, `COHORT_STOPPED` verdict `CONTINUE_INSUFFICIENT_EVIDENCE`,
and E1 has never placed an order. Realized P&L **$0.00**.

Restored to the authorized executable `f8fa6aa2` (fingerprint
`5e1413ca6c9af7c320f2be927971f3a4c0ddbf5e1691ca50d8b7cc289b451fe0`), normal
entrypoint `python3 -m scripts.l5_e1_cohort_run`, **DISARMED** — the three arm
keys blanked, the fingerprint pin left matching this build.
