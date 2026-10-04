# PMUS BTC15m — authoritative strike, reference and settlement semantics

Established from the venue's own documentation and metadata, then **verified
empirically against the venue's published values on all 28 development
windows**. Nothing here is assumed and nothing is substituted.

Sources: `docs.polymarket.us/faqs/crypto-faqs`,
`docs.polymarket.us/trader-guide/crypto-schema`, the per-market
`assetPriceTerms` object on `GET /v1/markets?slug=...`, each market's own
`description` field, and the Asset Prices API
`GET /v1/asset-prices/history`.

## 1. The reference index is BRTI — Coinbase is NOT the settlement source

> "Every market settles on CF Benchmarks' **Bitcoin Real-Time Index (BRTI)**, a
> regulated benchmark published **once per second**. Prices on spot exchanges
> or other indices have **no effect** on settlement."

Each market's own description repeats it: *"this market is about the price
according to CF Benchmarks' BRTI, not according to other sources or spot
markets."* The metadata carries `indexSymbol: "BRTI"` on every window.

**Consequence:** `data/btc_ref/*.jsonl` (Coinbase Exchange WS, 88,842 rows) is
a **proxy only** and must not be used as the reference. `src/btc_ref_recorder.py`
already says so in its own header. It is not needed for this research, because
of section 3.

## 2. Strike, statistic, and comparison — exact

| item | value |
|---|---|
| market family | Up/Down, one market per window, product code `cpc` |
| question | will BTC **close** the window at or above its **open** |
| **strike** | the window's **open value**, published as `priceToBeat` (Retail: `assetPriceTerms.priceToBeat`; Institutional: `price_to_beat`) |
| strike precision | **2 decimal places** (USD cents) |
| **statistic** | simple average of **60** one-second BRTI prices, rounded to 2 dp |
| computed | **twice** — once at window open, once at window close |
| **comparison** | long (UP) pays $1.00 iff `close >= open` |
| **ties** | `close == open` settles **UP** (long) |
| settlement timing | within ~1 minute of close; instrument expiration is 30 min later; **trading ends at the settlement instant** |
| tradable from | ~90 seconds **before** the window starts |
| open value fixed | a few seconds **after** the window starts |
| data integrity | no market settles on partial index data; settlement defers |

The docs describe the averaging interval as the inclusive `[T - 59s, T]`. In
terms of the Asset Prices API's **bucket-start timestamps** the equivalent set
is `[T - 60, T - 1]` — i.e. the 60 one-second buckets whose starts satisfy
`T-60 <= ts <= T-1`. That offset convention was **pinned empirically**, not
inferred (section 3).

Rounding is to the nearest cent; `ROUND_HALF_EVEN` reproduces every observed
value and is the same rule already confirmed for the fee table.

## 3. We do not need a proxy: the venue serves BRTI itself, keyless

`GET /v1/asset-prices/history` — "Reference index price history for crypto
price markets" — returns dense gap-filled OHLC candles, `security: []` (no
auth), with `ASSET_PRICE_BUCKET_1S` available. Limit is 3600 candles per
request, so a 15-minute window (900 candles) is one call.

```
GET https://gateway.polymarket.us/v1/asset-prices/history
      ?assets=crypto:btc&from=<unix_s>&to=<unix_s>
      &bucket=ASSET_PRICE_BUCKET_1S
```

Candles carry `timestamp` (unix seconds, bucket start), `open/high/low/close`
as strings, and `tickCount`. Gap-filled buckets carry `tickCount: 0` with close
carried forward — so **`tickCount` must be checked**, never assumed.

### Verification — 28/28 exact, to the cent

Reconstructing both statistics from 1-second closes over `[T-60, T-1]`:

| check | result |
|---|---|
| reconstructed open == venue `priceToBeat` | **28 / 28 exact** |
| reconstructed close == venue `settlementPrice` | **28 / 28 exact** |
| derived label (`close >= open`) == tape-derived settlement outcome | **28 / 28** |
| settlement-minute buckets present | 60 / 60 in every window |

**Quantified mismatch between our feed and the venue reference: zero.** The
series is the venue's own index, not a substitute, and the settlement statistic
is reproducible exactly rather than approximately. Provenance is the gateway
endpoint plus `assetPriceTerms`, both recorded per window.

Worked example — `cpc-btc-updown-15m-2026-09-30-1245z`: reconstructed open
84847.58 == `priceToBeat` 84847.58; reconstructed close 85281.14 ==
`settlementPrice` 85281.14; 85281.14 >= 84847.58, so UP, matching the tape.

## 4. Consequences for the state table — one of them is structural

1. **The strike is observed, never modeled.** `priceToBeat` is published a few
   seconds after window start. It is a legitimate, no-lookahead feature from
   that instant onward — and **is not available before it**, which bounds how
   early a first leg can be state-conditional at all.
2. **"Distance to strike" must use the index, and the right statistic.** The
   comparison is between two 60-second averages, so the natural distance is
   (running BRTI level) − `priceToBeat`, with the terminal comparison made
   against the close average rather than the last tick.
3. **The close statistic averages `[T-60, T-1]`, so the outcome progressively
   locks over the final minute.** At `T-60+k` seconds, `k` of the 60 prices
   entering the close average are already published. Conditional variance of
   the close falls deterministically across that last minute, and by `T-1` the
   outcome is fully determined. This is a real structural feature of the
   contract — and a serious **lookahead hazard**: any feature computed over the
   settlement minute must use only prices published at or before the decision
   instant. The state table will carry `seconds_remaining` and the count of
   already-published settlement-minute prices explicitly so this is auditable
   rather than implicit.
4. **Ties settle UP**, so `P(UP | state)` must be modeled as
   `P(close >= open)`, not `P(close > open)`.
5. **Settlement labels need no modeling at all**: `settlementPrice` and
   `priceToBeat` are both published per window, so the outcome label is read
   from the venue. It enters the research table as a **label only** and never
   as a decision feature.

## 5. Still open

- **Fee effective date remains unresolved** — code applies taker θ = 0.0695
  from 2026-09-17T04:00Z (CFTC notice) while current PMUS docs apparently say
  Oct 1. Unchanged by this work.
- **Index history retention** is not documented. Sep 30 windows resolve today
  (4 days back), but the retention horizon is unknown, so the BRTI path for any
  window of interest should be **fetched and persisted promptly** rather than
  assumed to remain retrievable.
- `tickCount: 0` gap-filled buckets did not appear in any settlement minute
  here, but the gap-fill rule means they can; the loader must not silently
  average carried-forward closes without recording that it did.
