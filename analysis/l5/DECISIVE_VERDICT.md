# DECISIVE VERDICT — L5's edge is fill quality, and it is now measured

The missing evidence was acquired and the decisive question is answered.
Historical replay on third-party public data; **no order was sent, nothing
here is realized profit for our account.** No live authorization sought.

## The data was already on disk

Zenodo 22084977 `updown_15m.parquet` — 1-second full Polymarket L2 ladders,
BTC/ETH 15m, 2026-08-21..24, CC BY 4.0 — was downloaded on 2026-10-04 and
sits in this session's scratchpad. **I verified its sha256 against the value
recorded in this repository before I looked:**

```
d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959  ✓ match
```

The CI acquisition workflow I dispatched is therefore **redundant for
access** but retained for **durability** — the scratchpad is ephemeral and
session-scoped, so committing the archive is what makes it survive.

Joined by `market_slug` + `outcome`: **3,069 L5 fills across 27 markets**,
independently reproducing the prior count of 3,074/27.

## THE ANSWER: explanation A. L5's fills are essentially non-toxic.

Settlement-based adverse selection, identical definition on both venues
(payout − price), market-level bootstrap because fills inside one market
are not independent trials:

| | adverse selection / fill | 95% CI | n |
|---|---|---|---|
| **L5 on Polymarket** | **−$0.0041** | **[−0.0251, +0.0194]** | 3,069 fills / 27 markets |
| **Gemini maker fills** | **−$0.2380** | **[−0.2831, −0.1935]** | 10,698 fills / 46 windows |

**The intervals do not overlap, and are not close to overlapping.** L5's
fills are statistically **indistinguishable from zero toxicity**; Gemini's
are decisively negative. The ratio of point estimates is **~58×**.

Corroborated independently of settlement by the forward book marks, which
show **no systematic post-fill drift**:

| horizon | +1s | +5s | +10s | +30s | +60s |
|---|---|---|---|---|---|
| median mark − fill price | +0.005 | +0.005 | −0.000 | −0.005 | −0.005 |

A toxic fill moves against you; these do not move at all.

### Why — the book L5 trades into is 14× tighter

Median single-leg spread **immediately before L5's fills: $0.0100**
(p10 0.01, p90 0.01, mean 0.0106; 172 crossed rows excluded and counted —
2.58% of btc/eth rows are crossed, an artifact of a stream with no sequence
numbers).

### And the fills are predominantly passive

| fill vs prevailing touch | n | share | adverse selection | CI |
|---|---|---|---|---|
| AT_OR_BELOW_BID (passive) | 1,887 | **61.5%** | −$0.0146 | [−0.0418, +0.0176] |
| AT_OR_ABOVE_ASK (mint) | 969 | **31.6%** | +$0.0103 | [−0.0394, +0.0684] |
| INSIDE_SPREAD | 194 | 6.3% | +$0.0259 | [−0.083, +0.1118] |

**All three classes are indistinguishable from zero.** So it is not that one
mechanism is clean and another toxic — the whole fill population is clean.

**Answer to the three-way question: A, decisively. Not B, not C.** Repeated
cycling is not rescuing toxic fills, because the fills were never toxic.
Quantitatively: A accounts for essentially the entire difference
(−$0.0041 vs −$0.2380 = $0.2339/fill of venue quality), and B contributes
nothing measurable because there is nothing to rescue.

### The mint discriminator survives a tautology check

A caveat was raised that complement books sum mechanically to 1.0000, which
would make any mint-feasibility test circular. **Verified directly on the
parquet, and it applies to the wrong sum:**

| sum | median | exactly 1.0000 |
|---|---|---|
| bid(X) + ask(complement) | 1.0000 | **97.8%** — tautological |
| ask(X) + bid(complement) | 1.0000 | **98.2%** — tautological |
| **bid(X) + bid(complement)** — the mint sum | **0.9800** | **0.8%** |

The mint-feasibility sum is **genuinely variable** (p10 0.92, p90 0.99), so
the 1.5% vs 99.86% discriminator in the prior finding is sound. The
complete-set mint is real and is how 31.6% of L5's fills price at or above
the prevailing ask.

## A finding neither I nor the venue scoring had noticed: Gemini's book DEGRADED 7×

Three separate disputes over "the Gemini spread" resolve into one fact —
both measurements were right, on **different periods**:

| source | span | n | median | p90 | share ≥ $0.20 |
|---|---|---|---|---|---|
| `gemini_bbo_btc05m.jsonl.gz` | **2026-08-30 → 09-02** | 61,497 | **$0.0200** | 0.02 | 2.1% |
| Oct-5 recorder p_snapshot | **2026-10-05** | 47,922 | **$0.1400** | 0.48 | **41.4%** |

**Five weeks ago Gemini BTC5M quoted a 2¢ book — comparable to
Polymarket's 1¢.** Today it quotes 14¢ with 41% of observations at or above
20¢. Every Gemini kill in this programme (complement reach 45.9%, ~4 prints
per window, −$0.238/fill) was measured on the **October** regime.

That does not resurrect Gemini — the current book is what we would trade —
but it reclassifies the failure from **structural** to **a current
liquidity condition that was materially better very recently**, which is a
monitoring trigger rather than a dead end.

Also corrected: the widely-quoted "Gemini median spread 0.14, bimodal" from
`STRUCTURAL_EXPANDED` is `spread_proxy_q1`, unit **"$ over 1.00"** — a
two-leg pair-cost excess, not a quoting width. My own top-of-book
measurement (median 0.14 on Oct 5) happens to agree numerically by
coincidence of period, not because they measure the same thing.

## Venue scoring against the ten derived requirements

**No independent venue passes.** Polymarket crypto up/down passes, and
circularly — the requirements were derived from L5's behaviour on it.

| venue | binding requirement |
|---|---|
| Polymarket crypto up/down | none microstructural; the gap is **evidentiary** (no book tape for most of the period) |
| **Gemini BTC5M** | **trade density** — venue-wide flow is ~5 prints per 5-min window against ≥30 own-trades/min needed, ~30× short; R2 independently forecloses (71.2% completion required vs 47.1% available) |
| Gemini BTC15M | **unmeasurable** — `gemini_bbo_btc15m.jsonl.gz` is UP-leg only, 23,318/23,318, zero DOWN rows |
| Kalshi 15M | **hard pair floor** — 0 sub-$1 in 2,269 rows *and* 0 in 2,409 independent rows, min 1.001 |
| Polymarket sports | **fees** — 1000 bps maker *and* taker, ~10¢ on a $1 pair against a 3.2¢ edge |
| PMUS | evidence gate, not a venue property |

Sequential sub-$1, plainly: **Polymarket crypto up/down CAN** — realized
VWAP pair below $1 in **3,518 of 5,100 matched events (69.0%)**, median
0.9679, tolerating p75 1.0136 en route. **Kalshi CANNOT** (floor 1.001).
**Gemini BTC5M does, but too slowly** — 0% inside 60s, median 174.6s.

## SCOREBOARD

| mechanism | cash | evidence | causal | executable | EV | capacity | incentives | $/day | status | next gate |
|---|---|---|---|---|---|---|---|---|---|---|
| **A: fill quality (1¢ book, ~zero toxicity)** | — | **3,069 book-joined fills / 27 mkts** | **yes** | **Polymarket only** | **−$0.0041/fill, CI spans 0** | — | excluded | — | **ANSWERED — this is the edge** | none; measured |
| matched-pair compression | +$80,652 | 6,252 events | yes | Polymarket | median pair 0.9679 | $316 median outlay | excluded | $195.96 | **MECHANISM CONFIRMED** | venue with the primitive |
| complete-set mint | — | 969 at/above-ask fills; mint sum median 0.98 | yes | Polymarket CTF only | +$0.0103/fill | — | — | — | **CONFIRMED, non-tautological** | — |
| directional residual | −$30,654 | 6,252 events | yes | yes | negative | — | — | — | **KILLED** | — |
| Candidate V1 EXTEND permission | — | 94,968 decisions | yes | yes | 99.71% vs clone 9.43% | — | — | — | **SUPPORTED** | owner grant (5¢ in 4 live bounds) |
| Candidate V1 switch timing | — | 0 of 17,096 OOS | **no** | — | — | — | — | — | **FAILS** | book-conditioned retest |
| Gemini BTC5M (Oct regime) | negative | 73 win, 0/88 cells, 10,698 fills | yes | yes | −$0.238/fill | — | $0 | negative | **KILLED at current liquidity** | **watch for a return to the Aug 2¢ regime** |
| Kalshi 15M | — | 2,269 + 2,409 rows | — | no mint, pair floor 1.001 | — | — | $0 credited | — | **KILLED** | — |

## GO / KILL

**KILL** — reproducing L5 on Gemini BTC5M or Kalshi **at current
conditions**. Not because the behaviour is wrong but because the venues
cannot supply the fills: Gemini is ~30× short on trade density and its
maker fills cost −$0.238 each; Kalshi has a hard pair floor at 1.001 and
pays $0 credited.

**GO** — on one narrow, cheap, non-trading action: **a two-leg (UP+DOWN)
book-and-print recorder on Gemini BTC15M**, plus a standing check on
whether BTC5M's spread returns toward the August 2¢ regime. BTC15M is the
only alternative habitat that passes everything its data can currently
measure (900s windows, median touch 500 shares, 2¢ spread) and whose
failures are **evidentiary rather than structural** — its one disqualifying
file is UP-leg only. That is a measurement, not a strategy, and it is the
only lever that could change the arithmetic.

**NOT GO** — no shadow, no live probe, no new strategy. Candidate V1 still
fails behavioural gate 1 on switch timing, and the reason is now sharper:
switch timing plausibly depends on **book state**, which we can now
measure for 3.5 of L5's 166 days. That retest is worth doing and is not a
new strategy.

## Housekeeping still open

The five Gemini underdog fills remain **SETTLED/CASH_CONFIRMED unaudited**
— real money with an unverified lifecycle. The audit script is hardcoded to
the canary ledger and needs a cohort parameter. Not blocking P0, but it is
the oldest open accounting item in the programme.
