# Gemini BTC5M / BTC15M regime monitor

Read-only. Public market data. **Nothing here is realized profit for our
account, and nothing here is trading authorization.**

> **A return toward the historical ~$0.02 single-leg regime is a RESEARCH
> TRIGGER, NOT TRADING AUTHORIZATION.** It means "re-open the BTC5m/BTC15M
> habitat question with fresh evidence". The live progression — historical
> replay → out-of-sample replay → realtime shadow → action-by-action
> comparison → bounded live probe → repeated clean probes → controlled
> scaling — still applies in full, from its first step.

Produced by `src/gemini_research/regime_monitor.py` (offline, over tapes).
Per-window records append to `windows.jsonl`; pooled per-tag distributions
live in `REGIME_MONITOR.json`. Rerun:

```
python -m src.gemini_research.regime_monitor \
  --tapes 'analysis/gemini/btc15m_rec/*.jsonl.gz' --tag <tag>
```

## The unit trap, stated before any number

* **single-leg spread** = `ask − bid`, top of book, on the one instrument the
  venue lists. Unit: **$ on one leg**.
* **pair-cost excess** = `up_ask + (1 − up_bid) − 1`. Unit: **$ over 1.00,
  two legs**.

`analysis/gemini/updown/STRUCTURAL_EXPANDED.json`'s `spread_proxy_q1` declares
its own unit as `"$ over 1.00"` — it is a **two-leg pair-cost excess, not a
spread**. It must never be compared against a single-leg spread. Every field
this monitor writes carries its unit inside the record.

(On a one-level book the two happen to coincide numerically, because
`up_ask − up_bid` *is* the pair-cost excess. They are still different
quantities and generalise differently past level 1.)

## What is recorded per window

| field | meaning | unit |
|---|---|---|
| `single_leg_spread` | `ask − bid`, top of book | $ one leg |
| `depth_top1_bid` / `_ask` | size at the best level | shares |
| `depth_top3_bid` / `_ask` | size summed over the top 3 levels | shares |
| `print_rate_per_min` | venue trade prints per minute of observed window time | prints/min |
| `complement_reach_price` | `1 − up_bid`: price to buy the **derived** complement leg | $ |
| `pair_cost_two_leg`, `pair_cost_excess_over_1` | two-leg cost and its excess over flat | $ |
| `book_state_counts` | TWO_SIDED / ONE_SIDED / EMPTY | count |

Each record is **threshold-free**: n, min, p10, p25, median, p75, p90, max,
`frac_ge_0_20`, `frac_le_0_02`. No field declares a regime change. One window
is one window; a regime statement needs the series, which is what
`windows.jsonl` accumulates.

## Independently verified baseline (2026-10-06)

All figures below were recomputed from the tapes in this repo, not quoted.

### Historical regime — `data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz`

* n = **61,497** quote rows, 400 BTC05M windows
* span **2026-08-30T23:18:46Z → 2026-09-02T13:39:34Z**
* single-leg spread: median **$0.0200**, p90 **$0.02**, **2.12 %** of rows ≥ $0.20
* depth at best bid / best ask: median **500 / 500** shares
* complement reach `1 − up_bid`: median **$0.52**; pair-cost excess median **$0.02**

This matches the briefed baseline (median 0.0200, p90 0.02, 2.1 % ≥ $0.20,
n = 61,497) **exactly**.

### Historical regime — `data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz`

* n = **23,318** quote rows, 136 BTC15M windows
* span **2026-08-30T23:18:46Z → 2026-09-02T13:39:34Z**
* single-leg spread: median **$0.0200**, p90 **$0.02**, **1.47 %** of window
  medians ≥ $0.20; 88.97 % of window medians ≤ $0.02
* depth at best bid / best ask: median **500 / 500** shares
* trade prints in this file: **0** — the file carries quotes only. Print rate
  from it is **UNAVAILABLE**; the separate print tapes are the source.

### Current regime — Oct-5 recorder tapes (`analysis/gemini/btc5m_rec/`, `btc5m_snap/`)

11 tapes, span **2026-10-05T13:32:16Z → 2026-10-05T20:49:27Z**.

| | BTC05M | BTC15M |
|---|---|---|
| windows | 86 | 28 |
| two-sided `p_snapshot` observations | 46,173 | 14,860 |
| single-leg spread, median of window medians | **$0.225** | **$0.08** |
| window medians p10 / p25 / p75 / p90 | 0.08 / 0.16 / 0.28 / 0.31 | 0.04 / 0.06 / 0.11 / 0.11 |
| fraction of window medians ≥ $0.20 | **61.6 %** | **0 %** |
| depth best bid / best ask (median) | **25 / 25** | **110 / 10** |
| depth top-3 bid (median) | 271 | 510 |
| complement reach `1 − up_bid` | $0.595 | $0.62 |
| print rate (median / p90, prints per min) | 0.359 / 0.901 | 0.451 / 2.603 |

Pooled over all `p_snapshot` rows in those tapes without per-window grouping,
n = **61,033** two-sided rows: median **$0.15**, p90 **$0.73**, **44.0 %**
≥ $0.20.

**Discrepancy declared.** The briefed current figure was n = 47,922, median
$0.1400, p90 0.48, 41.4 % ≥ $0.20. My independent recount over the same
directories gives n = 61,033 / median $0.15 / p90 $0.73 / 44.0 % pooled, or
n = 46,173 / median-of-window-medians $0.225 for BTC05M alone. I could not
reproduce n = 47,922 under pooling, per-duration grouping, or
`(market, lastUpdateId)` de-duplication (which gives n = 18,112). The *size*
of the degradation is unaffected: **a ~$0.02 median single-leg spread became
$0.15–$0.225, roughly 7–11x, in five weeks**, with best-level depth falling
from 500 shares to 25 (BTC05M).

**The most decision-relevant number here is the one nobody asked for:**
BTC15M's current median single-leg spread is **$0.08** with **0 %** of window
medians ≥ $0.20 and 110 shares at the best bid, against BTC05M's $0.225 /
61.6 % / 25 shares. On 2026-10-05 the 15-minute book was materially the better
habitat of the two. 28 windows on one day is a *hypothesis*, not a regime
finding — which is exactly what the BTC15M recorder is for.

## First live BTC15M two-sided tape — 2026-10-06, and the venue's own answer

Run `37393996336`, tape `analysis/gemini/btc15m_rec/btc15m_rec_20261006T002656Z.jsonl.gz`,
20 minutes, 2026-10-06T00:26:58Z → 00:46:57Z, 2 BTC15M windows, 3,994 rows
(900 `p_snapshot`, 838 `pair_snapshot`, 401 `p_depth`, 13 `p_trade`, 599
`window_poll`, 6 `down_probe`, 3 `strike_resolved`).

**The `down_probe` settles the UP-leg question from the venue's own mouth.**
Subscribing the hypothetical DOWN stream returns:

```
{"id":"dn","status":400,
 "error":{"code":-1013,"msg":"Invalid stream name: gemi-btc15m2610060030-down@depth20"}}
```

So **no separate DOWN instrument exists**. The complement can only ever be
derived as `1 − UP`, and the 23,318-row UP-leg-only file was not a recorder
that missed a feed — it was a tape that never wrote the derivation down. This
is now evidence rather than inference.

Measured on that tape (2 windows, 851 two-sided observations — far too few for
any regime statement):

* single-leg spread: median **$0.11**, 0 % of window medians ≥ $0.20
* depth at best bid: median **400** shares; top-3 bid **966**
* print rate: median **0.94** prints/min, 13 prints
* complement reach `1 − up_bid`: median **$0.615**; pair-cost excess **$0.11**
* book states: TWO_SIDED 426, ONE_SIDED 143, EMPTY 269

A worked `pair_snapshot`: UP bid `0.57 × 400`, UP ask `0.68 × 400` → derived
DOWN bid `0.32 × 400`, DOWN ask `0.43 × 400`; single-leg spread **$0.11**,
two-leg pair cost **$1.11**. Both legs, both units, one row.

`strike_resolved` fired as designed: `BTC15M2610060030` strike `85840.28`,
`availableAt 2026-10-06T00:15:00Z`, captured on re-read **717 s** after
availableAt — the authoritative value the first `window` row did not carry.

$0.11 is still 5.5x the historical $0.02. It is two windows on one night.
Nothing is triggered.

## Known measurement limits

* **Top-3 depth is UNAVAILABLE on the flat `*_bbo_*.jsonl.gz` tapes.** They
  record one level per side, so `depth_top3` equals `depth_top1` there by
  construction and must not be read as a 3-level measurement. Only the
  recorder tapes (`p_snapshot`, `pair_snapshot`) carry real ladders.
* **Print rate on quote-only tapes is UNAVAILABLE**, and the span used for a
  rate is the observed span of that window's rows, not its nominal life.
* **The complement leg is DERIVED, always.** The venue lists one instrument
  per window and it is `-UP`. `DOWN ask = 1 − UP bid`. No separate `-DOWN`
  instrument appears on any tape this programme has taken (121,492 of 121,492
  market-bearing rows in the Oct-5 recorder tapes are `-UP`).
* **Live verification was not possible from this container**: `api.gemini.com`
  is egress-denied here (CONNECT rejected, 403). Every figure above is from
  committed tapes; live confirmation happens in CI, where egress works.
* **No credential was used, and none is needed.** All of this is public book
  and print data. No step required an authenticated endpoint; had one, the
  work would have stopped there instead.

## The defect this programme must stop repeating

`data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz` is **UP-leg only: 23,318
of 23,318 rows `-UP`, 0 rows `-DOWN`** — confirmed. So is
`gemini_bbo_btc05m.jsonl.gz` (61,497 of 61,497). Because the venue lists only
the UP instrument, that is a *derivation gap* rather than a lost feed: the
complement was never written down, so every pair-cost conclusion drawn from
those two files rested on one leg plus an unrecorded assumption.
`src/gemini_research/btc15m_recorder.py` closes it by writing the raw UP
ladder, the derived DOWN ladder labelled `derived: true` with its formula, and
both the one-leg and two-leg quantities under their own names, in the same
`pair_snapshot` row — and by *probing* the venue for a real `-DOWN`
instrument each window and persisting whatever it answers.
