# L5 Round 5 — Gemini Maker Proxy Settled Against Official Outcomes

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `0b38030f`
**Pre-registration:** `a163c201`, committed **before** any markout was computed. **Mode:** read-only; no orders, deploys, services, spend; **no workflow dispatched**. Gate unchanged.

## The decisive finding, in one line

**Yes — the proxy can now be settled against official Gemini outcomes, and I did it. No — the resulting evidence does not justify continued validation.** n=50 proxy fills across 16 independent windows, with one window accounting for **106%** of discovery P&L and **947%** of holdout P&L, and **negative medians on both sides** in the holdout. The mean is positive and meaningless.

---

## 1. Your hypothesis was right, and the answer was already in the repository

I did not need the API. Official resolution data is **already committed** on `claude/gemini-btc-economic-viability-h3u7zl` under `analysis/gemini/**`:

```
resolutionSide : "no" x12,995   "yes" x9,988   (also 1,431 null = unresolved)
settlementValue: "81126.97848484849", "85738.27102564104", …  (index values)
resolvedAt     : "2026-09-19T01:40:07.057Z", "2026-09-22T02:30:06.989Z", …
```

Recovered: **201 distinct resolved event tickers**, 199 with `settlementValue`, spanning 2026-08-27 → 2026-10-04. Cells: BTC 5m 145, BTC 15m 28, ETH 5m 19, XRP 5m 3, SOL 5m 3.

**Ticker mapping confirmed empirically** (your step 2): market symbol `GEMI-<EVENT>-UP` → event ticker `<EVENT>`. Example `GEMI-BTC15M2609221300-UP` → `BTC15M2609221300`. No 404 was ever mistaken for missing data.

**This definitively refutes the settlement-gate workflow's header claim** that *"the venue publishes NO terminal value anywhere public."* Prior code on that branch already queried `/v1/prediction-markets/events/{ticker}` for exactly these fields and stored the results. The earlier probe tested `/settlements` (404) and account surfaces that were empty because we hold no positions — and generalised from that to the venue. Your reading was correct.

*(Note: `api.gemini.com` remained denied throughout — last 403 at 14:11:31Z. The allowance had not reached this container. It was not needed for this round.)*

## 2. Pre-registration was committed before measurement

`a163c201` froze, ahead of any result: settlement source and ticker mapping; payout from `resolutionSide` only (`yes` ⇒ UP pays $1, `no` ⇒ $0; null excluded, never imputed); strict quote-before-print causality; 3.0s freshness; removal of the terminal-quote condition; fill at the **quoted touch** with inside-spread prints excluded; size capped by displayed depth; transaction-level fee/rebate rounding with promo and post-promo reported separately; rebates excluded from the headline; **no minimum execution size**; 100% proxy fill as an explicit upper bound; discovery = Sep 21–22, holdout = Sep 23 + Oct 3–4. Nothing was altered after results were seen.

## 3. Results under the frozen estimator

Drops: `no_official_resolution` 300, `quote_stale_gt3s` 11, `inside_spread` 9.

### DISCOVERY — Sep 21–22
```
n=33 fills   11 independent windows   1,644 contracts
GROSS to official settlement  +$347.41  = +21.13c/contract
PROMO (maker fee 0)           +$347.41  = +21.13c/contract   [rebate, separate: +$8.23]
POST-PROMO (fee $7.18)        +$340.23  = +20.69c/contract
  we_buy   n= 9   808 ct    -3.65c/ct   (median  -7.00c)
  we_sell  n=24   836 ct   +45.09c/ct   (median +42.00c)
  CONCENTRATION: top window +368.53 of +347.41 = 106%;  largest single fill $207.06
  cells: BTC 5m 20, BTC 15m 13
```

### HOLDOUT — Sep 23, Oct 3–4 (rules unchanged)
```
n=17 fills   5 independent windows   952 contracts
GROSS to official settlement   +$26.83  = +2.82c/contract
PROMO (maker fee 0)            +$26.83  = +2.82c/contract   [rebate, separate: +$4.09]
POST-PROMO (fee $3.72)         +$23.11  = +2.43c/contract
  we_buy   n=12   856 ct   +2.74c/ct   (median -37.50c)
  we_sell  n= 5    96 ct   +3.49c/ct   (median -11.00c)
  CONCENTRATION: top window +254.12 of +26.83 = 947%;  largest single fill $149.38
  cells: BTC 5m 15, BTC 15m 2
```

ETH 5m, ETH 15m: **zero** qualifying fills — no per-cell result exists for them.

### Why this is not evidence of edge

1. **n=50 against a bar of 200.** Five independent windows in the holdout.
2. **Concentration destroys the mean.** One window is 106% of discovery P&L — i.e. the remaining ten windows are net negative. In the holdout one window is **947%** of total.
3. **Medians are negative on both sides in the holdout** (−37.50¢ buy, −11.00¢ sell) while means are positive. With 5 windows, the mean is a report on one lucky settlement.
4. **Discovery → holdout collapses** from +21.13¢ to +2.82¢/contract. Consistent with the discovery figure being an artifact.
5. **Fills are an upper bound.** Every one assumes our resting order filled because a print touched our level. Queue priority is unknown; the honest band remains 0–30%.

The one durable signal: `we_buy` is weak-to-negative in both periods (−3.65¢ discovery; +2.74¢ mean but −37.50¢ median in holdout), which is the third independent observation of buy-side adverse selection, after Polymarket (~−12¢/residual share) and the withdrawn round-2 proxy. Directionally consistent; still not decision-grade.

## 4. Capacity verdict — gate applied unchanged

**`UNMEASURABLE_ON_CURRENT_RIG`.** No numeric capacity may be assigned. Taking the holdout mean at face value would give 49,000 contracts/day × 10% capture × 2.82¢ ≈ $138/day — inside the $50–149 sidecar band — but that rests on a mean whose median is −37.50¢ and whose top window is 947% of the total. **I am not reporting $138/day as a capacity estimate.** Nothing here establishes net-positive expectancy for realistically fillable Gemini maker orders.

## 5. Two corrections accepted

**One-sided liquidity — I overreached, and the data is stronger than your correction.** I wrote that makers withdraw liquidity near expiry. I inferred a cause from an absence. Measured: **100% of 152,597 logged series rows are two-sided; zero one-sided rows exist.** `one_sided_obs` (median 95) is *counted* but prices are never logged. So the two-sided series ends where the *logger* stops recording, not provably where liquidity left. **One-sided making is structurally unobservable in this feed** — it can be neither evaluated nor excluded, and the market should not be discarded for it. This is also the first concrete reason the volume may hold otherwise-unrecoverable data; I have not touched it.

**No minimum execution size is imposed.** Round 4's "avoid 1-contract transactions" is withdrawn as a strategy rule. Rounding makes fees size-dependent — at P=0.50 post-promo, C=1 is −1.000¢/ct, C=10 is 0.000¢, C=25 is +0.080¢ — but optimal size depends on net spread, fill probability and inventory risk, none of which are established. It is reported as a cost curve only.

**On the workflow:** I did not dispatch `gemini-settlement-gate.yml` or any other workflow whose full execution path I had not audited, per your instruction. It turned out to be unnecessary.

## 6. The blocker has moved — and it is now small

The gap is no longer settlement *access*. It is settlement *coverage*:

| Already in hand | Count |
|---|---|
| Series windows harvested | **941** |
| Unique quote observations | **152,597** |
| Unique prints | **8,495** |
| Windows joinable (series ∩ prints) | **440** |
| Of those, with official resolution | **140** → only 50 fills survive the frozen filters |
| Dropped for `no_official_resolution` | **300 windows** |

The 201 stored settlements mostly predate Railway log retention (~Sep 20), so only ~75 are reachable. **We hold 440 joinable windows and need resolutions for 300 of them.** At the observed ~4 prints/window, resolving those 300 yields on the order of **1,200 candidate prints** — comfortably past n≥200 after filtering.

**Next concrete action:** allow `api.gemini.com` (still denied here) and fetch `resolutionSide`/`settlementValue`/`resolvedAt` for the 300 already-harvested windows that lack them — a read-only public GET per event ticker, no new component, no volume inventory, no deployment. Then re-run `a163c201` **unchanged** on the enlarged holdout. The estimator, mapping, fee model and split are already frozen and demonstrated end-to-end, so this is a data-fetch step, not a research step.

**If that still fails the gate**, I would pivot to PMUS sports breadth as you proposed, rather than spend another cycle on Gemini — the structural picture (10–25 contracts at touch, a third of each window unobservable, buy-side adverse selection in three independent datasets) is not improving.
