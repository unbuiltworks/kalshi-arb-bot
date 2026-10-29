# Closing the economic decision gap

**Bottom line: no candidate has confirmed positive net expectancy. One real
signal was found and it cannot be monetised by the engine we have. The binding
constraint is not strategy — it is data resolution, and I can name the exact
missing data.**

Nothing deployed. The live service is shadow-locked and unarmed. The approved
three-window corrected-executor validation is untouched and unexpanded.

### On whether to start a new session

No. The context is doing real work: it is why candidate A was rejected in one
pass, why the XRP05M artifact was caught, and why the taker-flow signal below was
caught as a row-split artifact. Your concern about shadow experiments is the
right one, though, and it has a specific answer — see *the measurement problem*.

---

## What already existed, and what I reused rather than rebuilt

| asset | state | used how |
|---|---|---|
| `src/gemini_maker/ref_lag_recorder.py` | built, running | its `data/ref_lag/` output is the whole of item 1 |
| `src/gemini_maker/fair_value.py`, `lag_study.py` | built | the reference-lag premise below |
| `data/policy_d_port*/` tapes | 63 files | Kalshi books + 593k aggressor-tagged prints |
| replay engine (`analysis/l5/taker_entry/replay.py`) | built last cycle | extended in place, not rewritten |
| Aug census (`ARB_STATUS.md`) | binding verdict | cross-venue arb **$0/day**, left closed |

**Pre-committed decision bands** (`ARB_EDGE_CENSUS_2026-08.md`, fixed before
results): **<$50/day → archive · $50–149 → sidecar · ≥$150 → scale**. $480/day is
stated there as "a business aspiration, not a pass/fail threshold." I ranked
against those rather than invent new ones.

## Item 1 — the settlement-probability estimator cannot be built. Exact reason.

The premise is real and mechanical, from the existing Gemini research: the
settlement source is **`GRR-KAIKO_RFR_<ASSET>USD_60S`, a 60-second *rolling*
mean**. So at τ seconds before expiry, a fraction **(60−τ)/60 of the settlement
value is already determined** by observed spot. At τ=10s, **83% is locked in**.
That is a concrete informational edge, not a model.

**It is not testable on the data we hold.**

| | |
|---|---|
| KXBTC15M windows with a strike | 70 |
| …inside BTC spot coverage at all | **39** |
| …of those, with **zero** spot prints in the final 60s | **25** |
| median spot prints in the final minute | **0** |
| windows with ≥6 prints in the final minute | 12 |
| BTC spot capture rate | **3.72 prints/60s** (16.1s mean gap) |
| the "1 Hz" reference proxy's actual rate | **27.3s mean gap** (~4% of nominal) |

The edge lives entirely in the final minute. We have no observations there for
64% of covered windows. **12 usable windows is the same trap XRP05M was, so I did
not run it and call it evidence.**

## Item 3 — attribution, measured separately

### Directional mispricing — rejected

232 Gemini market-windows, 115,271 book rows aligned to the settling reference
(signal and settlement sharing a source, so no basis argument can rescue it).

- The **market's own price is better calibrated than any model I built**: Brier
  0.201 vs 0.243, market winning 7/8 families and all 5 time buckets; model log
  loss 0.924 is worse than a constant 0.5 (0.693).
- Executable rule: **−$5,171.52 gross over 47,609 signals (−$0.1086 each)**,
  before fees.
- Median spread 12–33c against a median 60s move of 9.5c — off by an order of
  magnitude, not marginal.

### Stale market repricing — not found at available resolution

Kalshi's own taker-flow imbalance looked predictive on a row-level split
(test corr +0.0616, beta +2.38c at 30s). **It does not survive splitting by
market**, the independent unit: train −0.0192 → **test +0.0020**. A row-split
artifact. No stale-repricing edge established.

### Passive liquidity provision — the one with a real signal

**Book depth imbalance predicts the mid ~6.5s ahead, and it is robust:**

| test | result |
|---|---|
| market-level split | train corr +0.1460 / beta +0.843c → **test +0.1259 / beta +0.906c** |
| per-market betas (54 markets, ≥20 rows) | **48 of 54 positive (89%)** |
| significance, markets as the unit | mean **+0.933c**, SE 0.146, **t = +6.41** |

This is the only statistically robust signal found anywhere in this search, and
it is nothing like XRP05M (t=+1.39, 67% from one market).

**And it does not convert into profit.** Trading it through the pairing engine:

| policy (held-out BTC15m, 48 windows) | traded | clean | strand | net | per eligible |
|---|---|---|---|---|---|
| cheapest-first (current engine) | 25 | 23 | 2 | −1.2018 | −0.0250 |
| **book-imbalance side selection** | **47** | 43 | 4 | **−5.2488** | **−0.1093** |

**Why it fails is the important part.** +0.9c of predicted move against a 1.0c
spread is dead as a taker and free as a maker — but our P&L is a function of
**pair cost and completion**, not of mid direction. Predicting the mid gives no
mechanism to complete a pair below $1. It only raises participation (25 → 47
windows) at a worse per-trade number (−0.048 → −0.112).

> **We have a real signal and an engine that structurally cannot use it.**
> Monetising direction requires holding directional exposure deliberately —
> the opposite of what the pairing engine is for, and a new strategy.

## Items 2 & 4 — the best configuration, out of sample

| arm | TRAIN non-BTC (82) per elig | TEST BTC15m (48) per elig | strands on test |
|---|---|---|---|
| cheapest-first only | −0.0400 | −0.0330 | 6 |
| cheapest + defensive ≤1.20 | −0.0408 | −0.0250 | 2 |
| L5-band only | −0.0443 | −0.0405 | 3 |
| **L5-band + bounded defensive ≤1.20** | **−0.0370** | **−0.0094** | **0** |

This is the **first configuration that is best on both train and test** — the
ordering finally replicates. On held-out BTC15m it strands **nothing** (31 of 31
clean) and improves the current engine **2.7×**.

**It is still negative.** And the BTC15m cell is the favourable tail, not the
typical case:

| series | eligible | net | per eligible |
|---|---|---|---|
| KXETH15M | 50 | −2.9440 | −0.0589 |
| KXBTC15M | 48 | −0.4510 | **−0.0094** |
| KXXRP15M | 15 | +0.3007 | +0.0200 |
| KXSOL15M | 14 | −0.3121 | −0.0223 |
| KXDOGE15M | 3 | −0.0762 | −0.0254 |
| **all** | **130** | **−3.4826** | **−0.0268** |

1 of 5 series positive, on the smallest sample. Across all series:
**−$0.0268/eligible window → ≈ −$12.86/day** at 480 window-opportunities.

| requested metric | value (best arm, held-out BTC15m) |
|---|---|
| eligible opportunities | 48 windows; 31 entered (65%) |
| net after fees, depth, latency, queue fills | **−$0.4510** |
| expected net per opportunity | **−$0.0145** per entry · −$0.0094 per eligible |
| frequency | 96 windows/day/series |
| max drawdown | **$0.627** |
| capital required | ~$1.72 matched notional/window, $5 aggregate cap |
| calibration error | market Brier 0.201 vs model 0.243 — the market wins |
| evidence class | **replay**, corroborated by **settled live** (12 windows, −$1.3328) |

## Item 5 — outside BTC15m

No new venue infrastructure built. From existing collectors:

- **Gemini**: spreads 12–33c but **340 trades across 232 market-windows (mean
  1.5)**, with the majority of markets at **zero** (ETH15M 31/34, SOL15M 30/34).
  Unfillable.
- **Kalshi other series**: same one-tick spread with a **45–140× better queue**
  (median depth 33 / 10.7 / 12 vs BTC15m's **1,502**) and real flow — and
  expectancy has **no monotone relation to queue depth**. Every series negative.
  Queue position was my leading explanation for the 77.5% adverse-fill rate; it
  is measurably **not** the binding constraint.
- **Cross-venue / sports / props / weather**: **$0/day** certified by the August
  census (136.9M triggers → 1,661 decision observations → **0 at ≥2c, 0 at ≥3c**).
  Left closed.

## Item 6 — decision-level attribution

The replay now records, per decision: predicted probability (`p_model`),
executable price (`bid`/`ask` with depth), estimated edge, decision
(BUY UP / BUY DOWN / WAIT via `imb_min` conviction), actual modelled fill
(`fills[].kind` ∈ MAKER/TAKER/DEFENSIVE with queue and partials), settlement, and
net result split into **pair P&L vs residual P&L**. Files:
`analysis/profit_search/{extract_a,calib_a,lag_a,flow,replay_b}.py`.

---

## The measurement problem — this is the actual gap

Every edge hypothesised in three cycles has died at the same place: **the data is
coarser than the effect.**

| conclusion | capped by |
|---|---|
| latency unresolvable (λ=0.5/1/2s identical) | book snapshots every **21s** |
| "5s" imbalance horizon was really **6.5s** | same |
| settlement-probability estimator unbuildable | spot **3.72 prints/60s**, median **0** in the final minute |
| rolling-reference edge untestable | same |

Your worry that shadow experiments "don't reliably capture anything" is correct,
and this is why: **they are measuring at 21-second resolution an effect that
lives at sub-second resolution.** More shadow runs at this resolution will keep
producing this same answer.

### Exact missing data

1. **Full-rate BTC/ETH spot capture** — every trade, not ~1 per 16s. Needed for
   items 1–2. `src/gemini_maker/ref_lag_recorder.py` already does this; it is
   under-capturing.
2. **Tick-level Kalshi book snapshots on BTC15m** — every `bookTicker` update,
   not one per 21s. Needed for fills, queue position and latency.

Both are **configuration changes to collectors already running.** No new
service, no new engine, no capital.

## The one ranked opportunity, and the shortest honest route

**Ranked #1: passive liquidity provision — L5-band entry selection plus bounded
defensive completion.** Strongest evidence of anything tested: best on train and
test, ordering replicates, zero strands on held-out BTC15m, 2.7× better than the
current engine, max drawdown $0.63, and it needs no new capability beyond the
`DEF` module already in the codebase.

**It does not qualify for a bounded live profitability test, because it is
measured negative out of sample (−$0.0094/eligible window).** I am not going to
propose spending capital to confirm a loss, and defensive completion is a taker
action the deployed verifier currently forbids (`maker_only_forbids_defensive_
crossing: true`).

**Shortest route to earning a live test — three steps, no new infrastructure:**

1. **Raise collector resolution** on the two feeds named above. Days, not weeks;
   both services exist and run.
2. **Re-run items 1–4 at adequate resolution.** The estimator and replay are
   already written. This is the only step that can change a *sign* rather than a
   magnitude — everything else has been a magnitude.
3. **If, and only if, out-of-sample expectancy turns positive**, propose a
   bounded live test gated on the pre-committed **$50/day** band.

The approved three-window corrected-executor validation stays separate: it is an
execution-validation exercise for the lifecycle fix, not a profitability test,
and it remains unexpanded and still blocked at its own step-2 prerequisite.

## What the data supports about $480/day

**Zero, and the measured number is negative.** Four structurally independent
premises have now been tested:

| premise | result |
|---|---|
| locked / cross-venue arbitrage | **$0/day** certified (Aug census) |
| directional mispricing | **−$0.1086/signal** over 47,609 |
| stale repricing | no edge survives a market-level split |
| passive liquidity provision | **−$12.86/day** best arm across 5 series |

Matched pairs do earn **11.81% on deployed capital per window** — the mechanism
is genuinely profitable when a pair completes. But residual losses exceed pair
profits at every size, venue, series, queue depth and completion ceiling tested,
and **scale multiplies the sign you have.** $480/day would need ~$42.33 of matched
notional per window against today's $1.72, with a sign we do not yet have.

The honest statement is not "the target is unreachable." It is: **at the
resolution we currently measure, we cannot see an edge, and we have identified
exactly what resolution would be required to find out.**
