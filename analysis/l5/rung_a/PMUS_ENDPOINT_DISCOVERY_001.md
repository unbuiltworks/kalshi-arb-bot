# PMUS BTC15m endpoint discovery — read-only, 2026-09-28T22:31–22:35Z

No deployment, no build, no authorization, no strategy change. Every probe
below is an unauthenticated GET to the public gateway from this container.

## Two of my earlier statements were wrong. Corrected first.

**1. "The 2215z slug was selected before PMUS had listed it" — FALSE.**

The venue's own `createdAt` for `cpc-btc-updown-15m-2026-09-28-2215z` is
**2026-09-28T10:40:01Z**. I selected it at 21:42Z. It had existed for
eleven hours. My "I picked a window outside the venue's listing horizon"
was wrong, and the horizon measurement below shows why.

**2. "The resolver's routes do not serve this series at all" — FALSE.**

At 22:34:59Z, on the gateway:

| slug | `/v1/market/slug/` | `/v1/events/slug/` | `/bbo` |
|---|---|---|---|
| 1915z | 200 | 200 | 200 |
| 2000z | 200 | 200 | 200 |
| 2145z | 200 | 200 | 200 |
| 2215z | 200 | 200 | **404** |

I measured the earlier 404s *inside* the anomaly and generalised from them.
That was the same mistake as reasoning from a single window.

## Actual cause of the 2215z failure

A **transient venue-side 404 on all three metadata routes** for the 2200z
and 2215z slugs during roughly 22:15Z–22:31Z. Timeline:

| time | observation |
|---|---|
| 10:40:01Z | venue creates the 2215z market |
| 22:15:54–22:27:07Z | probe: `market_readable` LookupError, 11 attempts, both routes |
| ~22:19Z | my curl: `/market/slug/` 404, `/events/slug/` 404, `/bbo` 404 for 2215z |
| ~22:26Z | my curl: `/bbo` 404 for 2200z |
| 22:28:07Z | probe fails closed, unarmed, 0 orders |
| 22:31:56Z | `/market/slug/` 200 for 2200z and 2215z |
| 22:34:59Z | `/market/slug/` and `/events/slug/` 200 for every window tested |

`/bbo` for 2215z is **still** 404, so `/bbo` is not a reliable existence
oracle; it also 404s for every pre-open window (2245z, 2300z, 2330z,
2345z — all `MARKET_STATUS_CLOSED`). **`/v1/market/slug/` on the gateway is
the existence oracle**, not `/bbo`.

I cannot explain the anomaly from outside the venue and am not going to
invent a mechanism for it. What matters operationally is that it was
transient and that metadata for a window is available ~11.6 h in advance,
so it is survivable — see the proposal.

## Listing lead time, from the venue's own `createdAt`

No polling needed; the field answers it directly.

| window | createdAt | lead |
|---|---|---|
| 2026-09-28-2000z | 08:32:05Z | 11 h 28 m |
| 2026-09-28-2145z | 10:08:00Z | 11 h 37 m |
| 2026-09-28-2215z | 10:40:01Z | 11 h 35 m |
| 2026-09-28-2245z | 11:12:00Z | 11 h 33 m |
| 2026-09-29-0000z | 12:16:00Z | 11 h 44 m |
| 2026-09-29-0400z | 16:18:01Z | 11 h 42 m |
| 2026-09-29-0800z | 20:16:02Z | 11 h 44 m |
| 2026-09-29-1600z | absent (404) | — |

**~11.6–11.7 hours**, created in batches ~64 min apart. The horizon at
22:34Z lies between 2026-09-29-0800z and 1600z. Window selection has
roughly half a day of headroom; lead time was never the constraint.

## Endpoint matrix

| endpoint | host | auth | status | fields returned | required facts it proves |
|---|---|---|---|---|---|
| `GET /v1/market/slug/{market_slug}` | gateway | **no** | 200 for every window tested | `slug`, `status`, `active`, `closed`, `archived`, `outcomes`, `orderPriceMinTickSize`, `minimumTradeQty`, `question`, `category`, `endDate`, `createdAt`, `marketSides`, `feeCoefficient`, `id` | **exact slug identity** (echoed, matched locally), **state/tradability** (`status` + active/closed/archived), **tick size** (0.01), **quantity increment/minimum** (0.01), listing time |
| `GET /v1/market/slug/{slug}` | api | signed | not usable | — | none — public routes live on the gateway; unauthenticated it answers 401 |
| `GET /v1/events/slug/{event_slug}` | gateway | no | 200 (404 for 2245z at 22:31, 200 later) | `slug`, `ticker`, **`seriesSlug`**, `title`, `active`, `closed`, `archived`, `startTime`, `endDate`, `createdAt`, `markets[]` | **series identity** (`seriesSlug: "btc-updown-15m"`) — the only fact the market row lacks; everything else duplicates it |
| `GET /v1/markets/{slug}/bbo` | gateway | no | 200 only while OPEN/RESOLVED; 404 pre-open and for 2215z now | `marketSlug`, `currentPx`, `lastTradePx`, `settlementPx`, `bestBid`, `bestAsk`, `state` | contemporaneous top-of-book and **settlement price**. Proves no execution constraint and is NOT an existence oracle |
| `GET /v1/account/balances` | api | signed | 200 in the 2215z run | balances | account binding (proof #3, PASSED live at 22:15:54Z) |
| `POST /v1/order/preview` | api | signed | not exercised (it is a POST; needs authorization) | venue validation of a concrete order | the venue's own final word on tick/quantity/price. Already used by `pmus_micro_executor` and `PmusTransport.preview` |
| `GET /v1/events?limit=N` | gateway | no | 200, 47 KB | events page | nothing usable — ascending id, ~40 k rows; a bounded walk never reaches a new 15 m window |

Nothing here substitutes a historical constant: tick `0.01` and minimum
quantity `0.01` are read from this market's own row on every call, and
`market_micro_spec` already raises `MICRO_MARKET_METADATA_MISSING` when
either is absent rather than defaulting.

### UP/DOWN outcome mapping

The market row's `outcomes` is `["Yes","No"]` — the venue does **not** label
these Up/Down. The mapping is not discoverable from any endpoint and is
already established by the evidence chain in
`l5_order_body.STATIC_CONTRACT` (`yes_is: "UP"`, long pays when the close
is at or above the open) plus the observed live preview
`outcomeSide OUTCOME_SIDE_YES == ORDER_INTENT_BUY_LONG`. Preflight's
`outcome_mapping_is_up_down` already asserts that chain and does not
require the literal labels. **No change needed, and no endpoint to add.**

## Repo archaeology: a working path already exists

`src/pmus_micro_executor.py:142 market_micro_spec(t, slug, entry_price)`:

```python
resp = t.request("GET", f"/v1/market/slug/{slug}", base=q1.GATEWAY)
market = resp.get("market") ...
minq = _d(market.get("minimumTradeQty"))
tick = _d(market.get("orderPriceMinTickSize"))
```

**One call, gateway host, no fallback chain.** Callers:
`pmus_micro_executor` itself (`:269`, `:583`) and
`pmus_spring_l5_session` (`:1458`, `:3337`, `:3378`) — the paths that have
placed and settled real PMUS orders. `pmus_q1_executor`, the original live
executor, resolves no metadata at all: it reads `/bbo` for the book
(`fresh_bbo`, `:375`), exposes `tick_for_market` over the same
`orderPriceMinTickSize` field, and relies on `POST /v1/order/preview` as
the pre-trade validator.

`l5_pmus_transport.resolve_market` is a **second implementation** of this.
It tries the **api host first** — where these public routes are not served
— then `/v1/events/slug/`, then the gateway. It is not what broke 2215z
(the gateway route was 404 too, for both of us), but it is duplicate
surface, and its per-attempt failure is what made 2000z undiagnosable for
three hours.

## Proposals, smallest first. No code changed yet.

**P1 — warm the metadata cache before the window opens (smallest, highest value).**
`de72ab80` already keeps a per-slug metadata row and reuses it when a later
resolve fails; it only helps if the *first* resolve succeeded. The probe
boots ~29 min before the window and metadata is available ~11.6 h ahead, so
resolving once at process start would have made the 22:15–22:31 transient
survivable: the cache would have been warm, the failure would have printed
`PMUS_PREFLIGHT_DEGRADED`, and the window would have traded. One call moved
earlier; no new endpoint, no policy change.

**P2 — reuse the proven resolver instead of the second one.**
Point `PmusTransport.market()` at `market_micro_spec`'s single
gateway `GET /v1/market/slug/{slug}`, keeping `/v1/events/slug/` only for
`seriesSlug` and only when `market_series_is_btc15m` cannot be satisfied
from the slug (it currently can: `mk.get("seriesSlug") or mk.get("slug")`,
and the slug contains `btc-updown-15m`). This deletes the api-host-first
attempt and the fallback chain.

**P3 — nothing else.** `/bbo` plus the market row already supply every
required execution fact; no new endpoint is needed. `market_open_tradable`
already behaves correctly on this shape (`MARKET_STATUS_CLOSED` +
`closed: true` pre-open fails; `MARKET_STATUS_OPEN` + `closed: false` at the
open passes — verified live on 2230z at 22:31:56Z).

P1 and P2 together are one small diff in `src/l5_pmus_transport.py` plus one
call-site move in `scripts/l5_pmus_live_oneshot.py`. Awaiting your decision
before writing any of it.
