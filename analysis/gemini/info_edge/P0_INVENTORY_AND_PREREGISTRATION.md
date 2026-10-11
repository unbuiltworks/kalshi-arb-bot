# BTC5m INFORMATION-EDGE LANE — P0 data inventory and P5 preregistration

**Branched from** `115de70bc9ba451bb22488999084252eb8be03f8`.
**No P&L has been computed or inspected.** This document is written *before*
any strategy measurement, which is the point of P5.

## SCOPE SEPARATION — READ FIRST

The **Gemini BTC5m passive back-of-queue L5-style maker lane is CLOSED** and
is not reopened, modified, or appealed here. This lane tests a **different
strategy**: whether BTC underlying movement and a contemporaneous fair-value
estimate predict *executable* repricing of the Gemini BTC5m contract.

**A positive result here would NOT be evidence that the maker strategy
worked.** It would be a separate information-latency/mispricing strategy. A
negative result here does not revive the maker lane either.

No live orders. No maker/rebate optimization.

---

# P0 — DATA INVENTORY

## A. What already exists

| source | rows | content | timestamps |
|---|---|---|---|
| `data/ref_lag/` (41 files) | **303,134** `pbook` | Gemini prediction bookTicker: `b/B/a/A` (**executable** bid/ask + sizes), `c/C` last trade, `u`, `E` | `t_ns` receive + `E` venue ns |
| | **33,772** `settlement` | `settlement_value`, `resolution_side`, `source`, `expiry` — **ground truth** | `t_ns` |
| | **13,885** `spot_trade` | BTC/ETH/SOL/XRP spot trades, price, qty | `t_ns` receive + `ev_ms` venue |
| | **6,415** `spot_rfr` | our trailing-60s RFR **proxy**, 1 Hz | `t_ns` |
| | **1,172** `window` | `market`, **`strike`**, `strike_at`, **`source`**, `expiry`, `effective` | `t_ns` |
| | **1,041** `ptrade` | prediction contract prints | `t_ns` |
| `data/gemini_hifi/` (3 tapes) | ~37k | event-level `@depth` diffs + `@trade`, BTC5m, from the closed lane | `t_ns` + `E_ns` + `U`/`u` |

**Span of `ref_lag`:** 2026-08-31 13:28Z → 2026-09-01 14:02Z = **24.6 h**,
**179 BTC05M windows** with both window metadata and prediction book.

## B. Prior art — REUSE, DO NOT RESTART

Two existing modules already implement most of this hypothesis. Per the
repository's anti-drift rule I am reusing them rather than writing new ones:

- **`src/gemini_maker/lag_study.py`** — reference→prediction lag study with
  causal discipline already stated in its own docstring: fair value at `t`
  uses only spot with `t_ns <= t`; for horizon `h` the book state used is the
  last bookTicker **at or before `t_signal + h`**, i.e. the quote still
  resting when an order sent at `t_signal` arrives; IOC takes of stale quotes
  sized at `min(displayed depth, Q_CAP)`. Existing parameters:
  `HORIZONS_MS = (10, 25, 50, 100, 250, 500, 1000)`, `SIGNAL_DELTA_P = 0.05`,
  `STALE_EDGE_P = 0.03`, `TAKE_EDGE_P = 0.05`, `Q_CAP = 10.0`,
  `VOL_TRAIL_S = 300.0`. **Known defect: it models ZERO FEES** (its own
  docstring flags this).
- **`src/gemini_maker/fair_value.py`** — the P2 model, already transparent
  and analytic, not ML: driftless random walk, and for the 60 s averaging
  window `E[settle] = (realized_sum + spot_now·tau)/w`,
  `Var = sigma²·tau³/(3w²)`, `p_yes = Phi((E[settle] − strike)/sqrt(Var))`.

**I have deliberately NOT run either module's P&L**, because P5 requires
preregistration first. I read their parameters and structure only.

## C. THREE STRUCTURAL FINDINGS THAT CONSTRAIN THE WHOLE LANE

### C1. The settlement reference has CHANGED — the fair-value model is stale

| cohort | settlement source | evidence |
|---|---|---|
| historical (`ref_lag`, Aug 31–Sep 1) | **`GRR-KAIKO_RFR_BTCUSD_60S`** | 199/199 window rows, 6,355/6,355 settlement rows |
| **live (Oct 2)** | **`LS-LRRR-XBTUSD`** | 100% of live BTC05M contracts |

`fair_value.py` is built entirely around a **60-second rolling average**
(`RFR_WINDOW_S = 60.0`). **The live contract no longer settles on that
source**, and the averaging construction of `LS-LRRR-XBTUSD` is **UNKNOWN**.
Its variance term, its "realized share" logic and therefore the whole
late-window edge are **calibrated to a construction that has been replaced.**

**Consequence:** historical and live cohorts are **not poolable**, and the
model must be re-derived or re-validated against `LS-LRRR-XBTUSD` before any
economics. Establishing that construction is a P0 blocker, not a P2 detail.

### C2. The strike is NOT published before the window opens

Measured live: `strike.value = null` with
`strike.availableAt == startTime`. The threshold becomes known **at or after
T=0**, not before.

**Consequence:** pre-window fair value is impossible — you cannot price a
contract against an unpublished threshold. Any pre-window signal is ruled out
on data availability, not on performance. (Note this also independently
reinforces the closed lane's P7 finding, which reached the same wall from the
queue side.)

### C3. The available BTC spot source cannot resolve the requested horizons

`ref_lag`'s spot feed is **Gemini's own BTCUSD** (`SPOT_WS =
wss://api.gemini.com/v2/marketdata`). Measured inter-trade gaps over 24.6 h,
5,482 BTCUSD trades:

| gap | p10 | p25 | **p50** | p75 | p90 | max |
|---|---|---|---|---|---|---|
| seconds | 0.000 | 0.032 | **1.259** | 5.446 | 12.225 | 3,503 |

Only **29.7%** of gaps are under 100 ms and **34.2%** under 250 ms.

**At a p50 gap of 1.26 s, the requested 100 ms / 250 ms / 500 ms horizons are
NOT RESOLVABLE on this cohort.** A lead/lag result there would measure our
own sampling sparsity, not the market.

**Clock quality compounds it.** Receive-minus-event skew on `spot_trade`:
p10 **−149.6 ms**, p50 **−20.9 ms**, p90 **+571.5 ms**. The *negative* median
means our receive stamp sometimes precedes the venue event stamp — unsynced
clocks. **The skew band is as wide as or wider than the horizons under
test**, so leakage-free sub-second alignment is not establishable on
`ref_lag`.

## D. P0 SUFFICIENCY VERDICT

| question | existing data sufficient? |
|---|---|
| sub-second lead/lag (100/250/500 ms) | **NO** — spot p50 gap 1.26 s; clock skew ±150–570 ms |
| 1 s–5 s lead/lag | **MARGINAL** — resolvable but only ~3.7 BTC trades/min |
| fair value vs executable quote | **PARTLY** — model exists, but calibrated to a **replaced** settlement source (C1) |
| settlement ground truth | **YES** — 33,772 rows with value + side |
| pre-window signals | **NO, structurally** — strike unpublished (C2) |
| live-regime (`LS-LRRR-XBTUSD`) anything | **NO** — zero coverage |

**Therefore, per the instruction "if underlying BTC data is missing or
timestamp quality is inadequate, build a public-data shadow collector rather
than manufacturing historical alignment": a collector is being built and run.
No historical alignment will be manufactured.**

## E. The collector now running

`analysis/gemini/info_edge/ie_collector.py` — read-only, public data,
unauthenticated, **no order path in the file**.

| stream | content | why |
|---|---|---|
| Gemini `{sym}@depth` | every book **diff**, `U`/`u` sequence, `E` ns | executable book, verifiable ordering |
| Gemini `{sym}@trade` | every print | contract repricing |
| Gemini `{sym}@depth20` | periodic snapshot | seed/resync |
| Gemini events REST, 10 s | `strike`, `strike_at`, **`source`**, expiry, prices, `marketState`, settlement | threshold + ground truth + **when the strike actually publishes** |
| **Coinbase `matches`** | every BTC-USD trade: price, size, side, `sequence`, venue `time` | **measured ~4.7 trades/s — ~76× Gemini spot** |
| **Coinbase `ticker`** | `best_bid`/`best_ask` + sizes | executable reference, not mid |
| `clock_probe`, 60 s | local vs Coinbase server time, RTT | **measure** skew rather than assume it |

Reachability measured from this environment: Coinbase **200**, Kraken **200**,
**Binance 451 (geo-restricted)**, Gemini **200**. Subscribe lead 420 s so the
pre-window regime and the strike-publication moment are both captured.

Every row carries `t_ns` (receive) and, where the venue supplies one, the
venue event time. **Both are recorded; neither is substituted for the other.**

---

# P5 — PREREGISTRATION (frozen before any P&L is computed)

## 1. Hypotheses — ALL will be reported, including failures

| id | hypothesis | falsified if |
|---|---|---|
| **H1** | BTC moves first: a BTC return at `t` predicts Gemini contract repricing at `t+h` | no monotone relation survives OOS, or lead direction is ambiguous/reversed |
| **H2** | A causal fair-value estimate beats the **executable** quote by more than spread+fees+latency | OOS edge after costs ≤ 0 |
| **H3** | Contract **momentum** (its own prior move) predicts its next move | no OOS relation |
| **H4** | Contract **mean reversion** after a BTC-driven jump | no OOS relation |

**H3 and H4 are preregistered as competitors precisely so that I cannot
inspect the sample and then pick whichever of lead/lag, momentum, reversion
or fair value happened to pay.** All four get reported with the same
treatment whatever the outcome.

## 2. Fair-value model — v1, simple and transparent, no ML

Reuse `src/gemini_maker/fair_value.py` **structure**, with its averaging
window treated as a **parameter to be established, not assumed**:

```
tau = T - t ;  w = averaging window of the settlement source
tau >= w :  E = spot_now                       Var = sigma^2*((tau-w) + w/3)
tau <  w :  E = (realized_sum + spot_now*tau)/w  Var = sigma^2*tau^3/(3*w^2)
p_yes    =  Phi((E - strike)/sqrt(Var))
```

- `sigma` from trailing realized vol (`VOL_TRAIL_S = 300 s`, pre-set).
- **`w` must be established for `LS-LRRR-XBTUSD` from settlement residuals
  before any economics.** If it cannot be established, H2 is reported as
  **untestable in the live regime**, not estimated.
- **No ML.** If and only if the analytic baseline is established and its
  residuals are characterised may anything further be considered.

## 3. Preregistered buckets (no threshold optimization)

Fixed now, independent of outcome:

- **horizons:** 100 ms, 250 ms, 500 ms, 1 s, 2 s, 5 s (reporting which are
  resolvable per data source, per C3)
- **BTC return at `t`:** ±{0–2, 2–5, 5–10, 10–20, >20} bp
- **threshold distance** `|spot − strike| / (sigma·sqrt(tau))`: {0–0.25,
  0.25–0.5, 0.5–1, 1–2, >2}
- **TTE:** the P4 phases below
- **spread:** {1, 2, 3, 4–5, >5 cents}
- Continuous regression reported **alongside** buckets, not instead of them.

## 4. P4 window phases — fixed, never fitted

`0–60 / 60–120 / 120–180 / 180–240 / 240–270 / 270–285 / 285–300 s`.
Late-window is expected to matter most (the averaging mechanism in C1 implies
the settlement value is progressively pinned), **but no cutoff will be
selected from the same sample.**

## 5. Chronological split — declared now

| split | use |
|---|---|
| **DEV** | earliest 50% of collected windows — model construction, bug-finding |
| **VALIDATION** | next 25% — one confirmation pass |
| **OOS — UNTOUCHED** | most recent 25% — **read exactly once**, at the decision gate |

Chronological, never random (adjacent windows share regime). If OOS is read,
that read is final and is reported whatever it says. The historical `ref_lag`
cohort is a **separate regime** (C1) and may not be pooled with live data; if
used at all it is reported as its own cohort.

## 6. Execution rules — no touch=fill

- Signals act on the **executable** side: buying UP pays the **ask**; the
  economic complement is **selling UP** at the **bid**. Midpoint is never a
  fill price.
- A take fills only against **displayed size at that quote**, capped at
  `min(displayed_depth, Q_CAP)`. Size beyond displayed depth is **not**
  filled.
- The book state used for a decision at `t` with latency `L` is the last
  update **at or before `t + L`** — the quote still resting on arrival.
- **Fees, authoritative** (`maker 0.0175 / taker 0.07`, ceil-to-cent per
  fill; rebate `0.30 × 0.07 × C × P(1−P)`, rounded **down**, band
  $0.20–0.80): a directional take pays **`ceil(0.07 · C · P · (1−P))`**.
  **The existing `lag_study.py` models zero fees — that defect is corrected
  before any economics is reported.**

## 7. P3 latency grid — sensitivity, not a capability claim

25 / 50 / 100 / 250 / 500 / 1000 ms. **These are assumptions about what
*would* happen at a given latency, not claims about what this system can
achieve.** Per source: signals, executable fills, edge at submission, edge
after latency, settlement P&L, fees, net P&L, P&L/window, max drawdown,
directional exposure, win/loss distribution, and capacity capped by displayed
executable depth.

## 8. P6 economics — the target is expectancy, not a positive backtest

Report **net realized expectancy per 5-minute window**, then
`× 288 windows/day × realistic availability/capacity`. Reference ladder:
$0.25 → $72/day · $0.50 → $144 · $1.00 → $288 · $1.67 ≈ $480 · $2.00 → $576.

**Unfilled or theoretical signals are never annualized as realized profit.**
Labels stay strict: historical replay → shadow → live probe → submitted →
filled → settled → realized.

## 9. Decision gate — chosen once, against the untouched OOS

- **A. NO INFORMATION EDGE** — BTC movement/fair value does not predict
  executable repricing after latency and costs.
- **B. EDGE EXISTS BUT NOT EXECUTABLE** — prediction works; spread, latency
  or depth removes it.
- **C. EXECUTABLE SHADOW CANDIDATE** — untouched OOS shows positive net
  expectancy with defensible execution and sufficient opportunity/capacity.

**Only C advances, and only to realtime shadow. Never to live orders from
this analysis.**

## 10. Stopping rules declared in advance

- If `w` for `LS-LRRR-XBTUSD` cannot be established → H2 reported
  **untestable in the live regime**; no substitute model is fitted to make it
  testable.
- If a horizon is not resolvable by its data source (C3) → reported
  **unresolvable**, never estimated.
- If OOS contradicts DEV/VALIDATION → **OOS wins** and is reported as the
  result.
- Multiple-hypothesis exposure (4 hypotheses × 6 horizons × 5 buckets) is
  stated plainly; nominal significance on one cell is not a finding, and no
  cell is promoted without surviving OOS.

---

## WHAT HAS *NOT* BEEN DONE AT THIS POINT

- No P&L computed, inspected, or estimated.
- No model fitted.
- No threshold chosen from data.
- `lag_study.py` and `fair_value.py` **not executed**.
- No live orders; no order path exists in this lane's collector.
- The closed maker lane is untouched.
