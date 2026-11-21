# The market key that was "" — incident and correction

**2026-09-27.** Two orders adopted from the venue were booked into the
market key `""`. This record preserves what the original ledger actually
recorded and what the venue authoritatively says, side by side. **The
original ledger did not know these orders' market.** It is not amended to
pretend otherwise; the correction sits beside it.

## Authoritative venue records

Both from `/v1/order/{id}`, read by the GET-only recovery service
(fingerprint `d51b44a3fa83`):

| field | CR52P8GZ4YAQ | CR52QM4S6YBY |
|---|---|---|
| `marketSlug` | `cpc-btc-updown-15m-2026-09-27-0115z` | `cpc-btc-updown-15m-2026-09-27-0115z` |
| `side` / `outcomeSide` / `intent` | BUY / YES / BUY_LONG → **UP** | BUY / YES / BUY_LONG → **UP** |
| `state` | `ORDER_STATE_FILLED` | `ORDER_STATE_FILLED` |
| `cumQuantity` | 0.10 | 0.60 |
| `avgPx` | 0.1300 | 0.1400 |
| own-terms cost | 0.01300 | 0.08400 |
| `commission` | **0.0000** | **0.0000** |
| `insertTime` | 01:25:36.763Z | 01:25:36.617Z |
| `lastTransactTime` | 01:25:55.412Z | 01:25:52.357Z |

**0.70 UP shares, $0.09700, zero commission, no rebate.**

## What the local ledger recorded, and why

| | original (defective) | authoritative |
|---|---|---|
| accounting key | **`""`** | `cpc-btc-updown-15m-2026-09-27-0115z` |
| how it was derived | `str(row.get("market") or "")` | the venue's `marketSlug` |
| 0115z gross UP | 3.77 | **4.47** |
| 0115z UP leg cost | 1.459200 | **1.556200** |
| 0115z settlement P&L | +0.599500 | **+0.502500** |

The two fills were real and the money left the account at 01:25 — four
minutes before 0115z settled at 01:30. They were simply filed somewhere the
settlement could not see.

## Root cause

Adoption read the venue row's market as `row["market"]`. **This venue sends
`marketSlug`.** For an order we submitted, `mine.get("market")` supplies it
and the bug stays hidden; for a genuinely adopted order — one we never
submitted, where `mine` is empty — the market arrived `None`. Then
`resolve_pending_terminal` did `str(row.get("market") or "")`, and `None or
""` is a perfectly good dict key.

The telemetry hid it: `lifecycle()` emitted `e.get("market") or
transport_slug`, and `"" or slug` is the slug — so the orphaned fills
logged under whatever window the transport happened to be on.

## What it cost beyond the cents

`markets_with_exposure()` returned `[""]`, so `open_markets()` did too, and
`MAX_CONCURRENT_MARKETS` refused **every subsequent window**. The executor
stopped trading at 01:26 and spent **125 settlement reads** asking the venue
about a slug it has never heard of. `MAX_CONCURRENT_MARKETS` climbed 84 → 95
in four minutes while `settlement_read_error` climbed 100 → 125.

## Reconciliation, against confirmed reads only

`$59.78895` was an **expected** balance that was never confirmed — the
Railway connection dropped at that moment — and it must never be cited as
authoritative. The two confirmed reads spanning the incident:

```
59.18945   confirmed 2026-09-27T01:03:02Z   (after 0045z)
59.69195   confirmed 2026-09-27T02:01:19Z   (venue flat: 0 orders, {} positions, 0 unsettled)
delta      +0.50250

  0115z settlement as booked    +0.599500
  orphaned 0115z UP shares      -0.097000
  sum                           +0.502500    EXACT
```

No P&L was manufactured. The $0.09700 is two venue fill rows at venue
prices; the agreement is the check, not the construction.

## Corrected live sample

| market | booked | corrected |
|---|---|---|
| 0000z | −0.888600 | −0.888600 |
| 0015z | −0.839900 | −0.839900 |
| 0030z | −0.180000 | −0.180000 |
| 0045z | −0.328000 | −0.328000 |
| 0115z | +0.599500 | **+0.502500** |
| **total** | −1.637000 | **−1.734000** |

matched **+0.189754** · residual **−1.923754** · programme cash from the
$61.69765 anchor **−2.00570**. The orphan is pure UP residual, so the
matched line is unchanged.

## The repair

A single canonical boundary. `canonical_market()` accepts only a validated
slug and raises on `None`, `""`, whitespace or an unrecognised shape;
`venue_row_market()` reads `marketSlug` **by name** and falls back to a
local record only when one exists. Every money-bearing path validates
before it mutates: `apply_fill`, `apply_order_progress`,
`resolve_pending_terminal`, `leg_cost`, `risk_exposure_usd`,
`cash_deployed_usd`, `retire_settled_market`.

An order whose identity cannot be established is **QUARANTINED**: it leaves
`live_orders` and `pending_terminal`, its raw venue row is kept verbatim,
it contributes exactly zero to financial state, and the book stays
uncertain — which refuses every submission — until a human resolves it.

The telemetry fallback is gone. Missing identity now logs as
`market: null, market_identity: "MISSING"`.
