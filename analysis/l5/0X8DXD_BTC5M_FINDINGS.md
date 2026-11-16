# 0x8dxd BTC5m replication — measured findings

**Status: research. Nothing armed, nothing money-capable deployed, no live
authorization requested or implied by this document.**

> **⚠ MARCH FIGURES IN THIS DOCUMENT ARE SUPERSEDED.** They were
> computed on a capture truncated by the 300-page cap, which held only a
> fifth of March. The untruncated re-capture (`PAGE_CAP=1500`, 750,500
> rows, 2026-03-12 → 2026-08-25) gives March 2,734 BTC5m windows and
> 392,634 fills, not 421 and 87,320. Corrected figures, and the verified
> latest trading date, are in `0X8DXD_VS_OUR_STRADDLE.md`. The AUGUST
> era and every structural conclusion below were unchanged by the fuller
> tape; only March's magnitudes moved.

Accounting label for every figure below: **historical reconstruction from
public venue data**. None of it is our realized P&L.

---

## 1. Identity — VERIFIED

`proxy_wallet 0x63ce342161250d705dc0b16df89036c8e5f9ba9a` returns
`name: "0x8dxd"`, `pseudonym: "Blushing-Fine"` on three independent
data-api v2 routes (`/trades`, `/activity`, `/positions`). The
association was checked, not assumed.

## 2. Data sources, and which tape is trustworthy

| Source | Role |
|---|---|
| `activity.jsonl` | **The fill tape.** TRADE + REDEEM in one schema, so fills and settlement cashflows reconcile within one source. |
| `resolutions.jsonl` | **Authoritative per-window winner**, from the CLOB market record's `winner` flag per token. 4,586 windows fetched, 1 error. |
| `trades.jsonl` | **NOT the fill tape.** Partial. |
| `positions.jsonl` | **Unusable for history.** The v2 positions route returns only *currently open* positions — 12 rows against ~4,000 traded windows. |

### `/trades` is a partial tape — measured, not assumed

For `btc-updown-5m-1787613900`, `/trades` returns **1 of 9 fills** (44 of
701.54 contracts). Across the 400 windows both routes cover, activity
holds more fills in 230 and fewer in **zero**. Reconciled against
redemption size:

| tape | windows with a redemption | cost basis reconciles |
|---|---|---|
| `/trades` | 688 | 166 (24%) |
| `/activity` | 1,276 | **848 (66%)** |
| union | 1,280 | 590, with 269 *over*-reconciliations |

The union double-counts, so the tapes are **not merged**. All figures
below use `activity.jsonl` alone.

### Two measurement biases found and corrected

1. **Resolution must not come from redemptions.** A window where
   0x8dxd held only the *losing* side pays no redemption. Defining
   "settled" as "has a redemption row" excludes the losing windows by
   construction and manufactures a ~100% win rate. Measured: that bias
   dropped 429 of 1,281 windows and reported a 99.9% win rate. Winners
   now come from the venue's own `winner` flag, independent of the
   trader's redemption behaviour.
2. **Truncation drops the oldest history.** v2 pages newest-first, so a
   page cap truncates the *earliest* data. The activity tape begins
   2026-03-23; windows before that are excluded rather than reported
   with an incomplete tape.

### Known unrecoverable gap

In 414 windows (all March) the redemption **exceeds** the tape's
terminal inventory — $30,457 of settlement cash with no captured cost
basis. (On the untruncated tape this is **2,514 of 2,719** March
windows, 92%.) The signature (one outcome positive, the other negative, sells
exceeding buys) is consistent with **SPLIT** operations, which mint both
tokens for $1 and appear as no trade on either route. These windows'
cost basis is not recoverable from these APIs, so their P&L is reported
separately and **not trusted**.

## 3. Two BTC5m eras, and they are opposites

0x8dxd traded BTC5m in two disjoint periods, with nothing in between.

| | MARCH (accumulation) | AUGUST (97–99¢ favorite) |
|---|---|---|
| windows (in tape coverage) | 421 | 860 |
| fills per window | **207** | **4.0** |
| size-weighted entry price | **$0.4312** | **$0.9885** |
| median fill time | t+93s | **t+291s** |
| fills in last 60s | 6.7% | **100%** |
| sells | 16,354 | **0** |
| buys both outcomes in a window | yes, routinely | no |

**The 97–99¢ late-favorite strategy we have been building all session is
0x8dxd's final era — and it did not make money.**

## 4. Settled P&L by era and cost-basis integrity

1,281 settled windows (venue winner known, tape complete):

| era | cost basis | n | fills/win | win % | net | return on cost |
|---|---|---|---|---|---|---|
| AUGUST 97–99¢ | **reconciled** | 851 | 4.0 | 99.6% | **+$1,533.83** | **+0.27%** |
| AUGUST 97–99¢ | no redemption (held loser only) | 6 | 1.0 | 50.0% | **−$2,080.51** | −49.77% |
| AUGUST 97–99¢ | unreconciled | 3 | 11.0 | 100% | +$0.82 | +0.10% |
| MARCH accumulation | unreconciled | 411 | 209.7 | 61.1% | +$27,336.15 | +2.97% |
| MARCH accumulation | reconciled | 10 | 111.6 | 30.0% | −$3,029.36 | −25.24% |

### The August 97–99¢ strategy is net negative

**860 windows, net −$545.86.** 851 redeemed windows accumulated
+$1,534 of pennies; **six** windows where the favorite lost cost
−$2,081 and erased all of it. This is exactly the shape a 99¢ strategy
has: break-even needs a 98.85% win rate at their vwap, one loss cancels
roughly 86 wins, and six losses in 860 windows is slightly worse than
break-even. 0x8dxd's last BTC5m fill is 2026-08-24.

### The March accumulation strategy cannot be evaluated from public data

411 of 421 March windows have missing cost basis (SPLIT). The +2.97% on
those windows is an artifact of understated cost, not a measurement. The
10 windows with verified cost basis are **−25.24%** on n=10. **The March
strategy's profitability is UNKNOWN, in both directions, and is not
recoverable from these APIs.**

### Whole-capture caution

The apparent profit lives entirely in windows where cost basis is
missing. On the untruncated tape: the 1,056 windows whose cost basis is
fully verified read **−$37,504**, while the 2,517 that do not reconcile
read **+$193,820**. **Neither number is a valid estimate of the March
era** — whether a window reconciles is determined by whether SPLIT was
used in it, which is part of the strategy itself. **This capture does
not support a claim that 0x8dxd was profitable on BTC5m**, and does not
support the opposite claim for March either.

## 5. The 44-contract Down @ $0.99 fill, in context

`btc-updown-5m-1787613900`, opens 2026-08-24 23:25:00Z, venue winner
**Down**. Full tape, reconciled exactly to redemption:

| t+s | side | outcome | price | size | inventory after |
|---|---|---|---|---|---|
| 286 | BUY | Down | 0.99 | 44.00 | 44.00 |
| 288 | BUY | Down | 0.99 | 22.53 | 66.53 |
| 289 | BUY | Down | 0.99 | 100.00 | 166.53 |
| 292 | BUY | Down | 0.99 | 15.12 | 181.65 |
| 292 | BUY | Down | 0.99 | 113.63 | 295.28 |
| 294 | BUY | Down | 0.99 | 100.00 | 395.28 |
| 300 | BUY | Down | 0.99 | 51.31 | 446.59 |
| 300 | BUY | Down | 0.99 | 5.95 | 452.54 |
| 306 | BUY | Down | 0.99 | 249.00 | 701.54 |

Cost $694.56, redemption $701.54 (observed, agrees), **net +$6.98**.

The 44-contract fill is not a decision — it is the **first of nine**
fills in a 20-second accumulation to 701.54 contracts at a flat $0.99.

**Executable consequence: our runner cannot participate in this at all.**
`CANCEL_LEAD_S=30` sweeps our orders at t=270s. Every fill above is
t≥286, and three are at or past the nominal close (t=300, 306). 100% of
0x8dxd's August fills are in the last 60 seconds, median t+291s. Our
cancel lead structurally excludes us from the entire region where this
strategy operated. Since that strategy is also net negative, the
conclusion is not to move the cancel lead.

## 6. Maker vs taker — UNOBSERVABLE

Neither v2 route carries a role field. Not inferred from price: a fill
at $0.99 could be a resting bid that was hit or an offer that was
lifted, and those are opposite behaviours. The two routes return
overlapping but distinct row sets (9,519 trades-only and 87,297
activity-only rows in the same period), which *might* reflect a
role-based split, but that is a hypothesis with no venue documentation
behind it and is not used in any figure here.

## 7. Smallest repeated behavior replicable with $2 clips

**There isn't one with measured positive expectancy.** A single isolated
buy held to settlement — which is exactly what a $2 one-clip-per-window
order is:

| era | n | win % | return on cost | mean per fill | their avg clip |
|---|---|---|---|---|---|
| MARCH, first buy of window only | 421 | 51.31% | **−1.04%** | −$0.26 | $25.38 |
| AUGUST, first buy of window only | 860 | 99.30% | **−0.40%** | −$1.56 | $386.87 |

Negative in both eras. Per-fill by price bucket tells the same story:
the only positive bucket of any size is March `px<0.60` at **+3.49%**
(58,828 fills, 38.52% win rate) — and that is the bucket inside the
207-fill, two-way, both-outcomes accumulation sequence whose cost basis
we cannot verify and whose $990 median per-window deployment our $5
per-market ceiling cannot express by a factor of ~200.

**Conclusion.** The profitable-looking behavior is a high-frequency
two-way accumulation sequence, not a clip. Its edge, if real, is a
property of the sequence; the first entry of the sequence loses money on
its own. Nothing here identifies a $2-clip behavior to deploy, and the
97–99¢ strategy Stage 1 was built around is measurably net negative over
its author's own 860 windows.

## 8. What would change this

1. **On-chain reconstruction of the March era.** SPLIT/MERGE operations
   are the missing cost basis. They are on Polygon; the data API does
   not expose them. Without them the March strategy stays unmeasurable.
2. **Sequence-level replay, not clip-level.** If the March edge is real
   it must be tested as a sequence, under our ceilings, which currently
   cannot hold one.

## 9. Reproduce

```
python3 analysis/l5/competitor_0x8dxd_fetch.py activity trades   # PAGE_CAP, OUT_DIR
python3 analysis/l5/competitor_0x8dxd_resolution.py              # resumable, cached
python3 analysis/l5/competitor_0x8dxd_reconstruct.py summary
python3 analysis/l5/competitor_0x8dxd_reconstruct.py first20
python3 analysis/l5/competitor_0x8dxd_reconstruct.py detail btc-updown-5m-1787613900
```

Reports: `analysis/l5/reports/0x8dxd_first20_settled_windows.txt`,
`analysis/l5/reports/0x8dxd_window_44at99_1787613900.txt`.
