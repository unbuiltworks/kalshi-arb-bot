# Profitability search: three candidates, measured. All three negative.

Objective changed from replicating L5 to finding the most profitable executable
strategy on existing infrastructure. **No new services, no new engine, no capital
change, no live orders. The live service remains shadow-locked and unarmed; the
three-window maker authorization is untouched and unexpanded.**

Ranked against the **pre-committed August bands** (`ARB_EDGE_CENSUS_2026-08.md`),
not thresholds invented after seeing results: **<$50/day → archive · $50–149 →
sidecar · ≥$150/day → scale**, with $480/day explicitly "a business aspiration,
not a pass/fail threshold."

---

## Ranking by verified executable net profitability

| rank | candidate | net per opportunity | projected $/day | evidence | verdict |
|---|---|---|---|---|---|
| **1** | **B selective inventory** | **−$0.0250 / eligible window** | **−$2.40** | replay + settled live | least negative |
| 2 | C venue/market selection | −$0.0350 / eligible window | −$16.80 across 5 series | replay + prior census | no venue rescues it |
| 3 | A directional mispricing | −$0.1086 / trade | heavily negative | replay | reject outright |

**None is positive. All three fall in the pre-committed `<$50/day` archive band —
and not at the boundary: they are negative.**

---

## A — DIRECTIONAL MISPRICING — rejected

**Dataset.** 232 Gemini market-windows (BTC/ETH/SOL/XRP × 5m and 15m),
**115,271 book rows** aligned to `spot_rfr`. This is the strongest possible
setup: `spot_rfr` **is** `GRR-KAIKO_RFR_BTCUSD_60S`, the series the market
settles on, so signal and settlement share a source and no cross-venue basis can
explain a result away. Strictly causal — trailing volatility only.

| metric | value |
|---|---|
| eligible opportunities | 47,609 trade signals at edge ≥0.05 · **true independent n = 232 settlements** |
| net P&L (gross, before fees) | **−$5,171.52** · −$0.1086/trade |
| at edge ≥0.20 | −$1,704.14 · −$0.1244/trade |
| frequency | ~226 market-windows/day |
| max drawdown / capital | not meaningful — monotone losing |
| evidence | **replay** |

**Three independent kills:**

1. **The market's own price is better calibrated than the model.** Brier 0.201
   (market) vs 0.243 (model); the market wins **7 of 8 families and all 5
   time-to-expiry buckets**. Model log loss 0.924 is worse than a constant 0.5
   (0.693) — it is confidently wrong.
2. **No lead-lag.** Reference move → next book move: correlation ≈ 0, and
   **−0.10 at 60s, the wrong sign** (mean reversion). Hit rate 0.47–0.50.
3. **The spread dwarfs any signal.** Median spread **12–15c (15m)** and
   **27–33c (5m)**, against a median 60-second mid move of 9.5c. The cost of
   acting exceeds the whole distribution of moves.

**The one apparent exception, honestly dissolved.** XRP05M looked strongly
positive and monotone in threshold (+$0.13 → +$0.31/trade, 79–85% hit, n=2,140).
It is **16 markets, not 2,140 trades** — and **one single market is 67% of the
P&L**, 10/16 profitable, **t = +1.39**. One lucky 5-minute window, amplified by
470 autocorrelated looks at the same settlement.

> **Methodological warning that applies to all of this work, mine included:**
> per-observation counts in book replay overstate the independent sample by
> roughly **100×**. The unit of independence is the *settlement*, not the row.

## B — SELECTIVE INVENTORY MANAGEMENT — least negative, still negative

Reuses the existing pairing engine and the already-built `DEF` module
(`defensive_pair_ceiling 1.20`, currently disabled; the verifier confirms
`maker_only_forbids_defensive_crossing: true`). 130 independent windows,
2026-08-30 → 09-01. **Trained on 82 non-BTC, evaluated on 48 held-out BTC15m.**

### Maker completion vs holding residual vs bounded defensive completion

| arm | traded | clean | strand | defensive fills | **net (held-out)** | per traded |
|---|---|---|---|---|---|---|
| B1 maker completion only | 25 | 19 | 6 | 0 | −1.5849 | −0.0634 |
| B2 defensive ≤1.00 (locked profit) | 25 | 20 | 5 | 1 | −1.4811 | −0.0592 |
| B3 defensive ≤1.05 | 25 | 22 | 3 | 8 | −1.3334 | −0.0533 |
| **B4 defensive ≤1.20 (`DEF` cap)** | 25 | 23 | **2** | 9 | **−1.2018** | **−0.0481** |
| B5 defensive ≤1.20, whole window | 25 | 25 | 0 | 25 | −1.6649 | −0.0666 |

**The mechanism genuinely works:** B1→B4 is monotone, worth **+$0.38**, and cuts
strands **6 → 2**. Crossing too early (B5) destroys it, so the 540-second arming
window matters. This is the only component in the whole search with a
consistent, mechanically-explained held-out improvement.

**But it is loss reduction, not profit.** And the ordering does not replicate on
TRAIN (B1 −3.2799, B4 −3.3419, B5 −2.7100 — non-monotone), so the ceiling choice
is not robust.

### Selective entry does not flip the sign

On TRAIN, **every bucket of every entry feature is negative** — spread,
|mid−0.5|, depth, opening price, time-to-expiry. Best achievable ≈ −$0.02/window.
Carried out of sample:

| arm | traded (of 48) | net | per traded | per eligible |
|---|---|---|---|---|
| B4 no entry filter | 25 | −1.2018 | −0.0481 | −0.0250 |
| + spread ≥0.02 (train-best) | **5** | −0.4910 | **−0.0982** | −0.0102 |
| + spread & \|mid−.5\| | **2** | −0.0649 | −0.0325 | −0.0014 |
| + tte ≤600s | 22 | −0.8094 | −0.0368 | −0.0169 |

The filters cut total loss **only by ceasing to trade** — the best one enters 2
of 48 windows. And the train-fitted filter **inverted**: −0.0229/window on train
→ **−0.0982/window** on test, worse than no filter at all. That is overfitting on
n=54, caught by the split.

| metric | value |
|---|---|
| eligible opportunities | 48 held-out windows; 25 entered (52%) |
| net after fees + queue fills | **−$1.2018** |
| expected net per opportunity | **−$0.0481** per entry · −$0.0250 per eligible |
| frequency | 96 BTC15m windows/day → ~50 entries/day |
| max drawdown | **$1.213** |
| capital required | ~$1.72 matched notional/window, $5 aggregate cap |
| out-of-sample | negative at every ceiling and every filter |
| evidence | **replay**, corroborated by **settled live** |

**Settled-live corroboration:** 12 live windows on 2026-10-25 settled
**−$1.3328** (−$0.111/window) — same sign as the replay, 4.4× worse. The replay
is, if anything, optimistic.

## C — VENUE AND MARKET SELECTION — no venue rescues it

### Gemini: wide spreads that cannot be filled

| family | median spread | mean trades / market-window | markets with **zero** trades |
|---|---|---|---|
| BTC05M | 0.270 | 6.6 | 5/27 |
| BTC15M | 0.120 | 1.0 | 22/34 |
| ETH15M | 0.150 | 0.2 | **31/34** |
| SOL15M | 0.140 | 0.2 | **30/34** |
| XRP15M | 0.130 | 0.2 | **29/33** |

**340 trades across 232 market-windows — mean 1.5.** A 12–33c spread with no
flow is not an opportunity. Kalshi BTC15m, by contrast, prints **~8,985 per
market**: roughly 6,000× the flow at one tick.

### Kalshi: the one promising cell, checked and closed

Our live BTC15m order rested behind **`queue_ahead: 5023.03`** for 1.4 contracts
— which is why 96.3% of our fills are swept and 77.5% adverse. So the obvious
hypothesis: find a series with real flow and an *attainable* queue.

It exists:

| series | median spread | **median queue** | prints/market | **net per eligible window** |
|---|---|---|---|---|
| KXBTC15M | 0.0100 | **1,502** | 8,985 | −0.0250 |
| KXETH15M | 0.0100 | **33** | 1,494 | −0.0529 |
| KXSOL15M | 0.0100 | **10.7** | 941 | −0.0191 |
| KXXRP15M | 0.0100 | **12** | 906 | −0.0236 |
| KXDOGE15M | 0.0200 | 19 | 554 | −0.0242 |

A **45–140× better queue position at the same tick, with real flow** — and it
**does not help**. Expectancy has no monotone relationship with queue depth
(10.7 → −0.0268/traded; 33 → −0.0827; 1,502 → −0.0481). Every series is
negative: **−$1.83 to −$5.08 per day per series**, −$16.80/day across all five.

Queue position was my leading structural explanation for the adverse-selection
problem. It is measurably **not** the binding constraint. The pair economics are.

### Already settled, not re-run

The August census certified **$0/day** for cross-venue locked arb (136.9M raw
triggers → 1,661 decision observations → **0 at ≥2c and 0 at ≥3c**), clean MLB
taker/taker, props/incentives, G↔K and weather. That verdict stands and I did not
spend time reopening it.

---

## The one candidate, and the shortest honest path

**Selected: B — but as a loss-reduction mechanism, not a profit strategy.**

Bounded defensive completion is the only component in this search that produced a
consistent, monotone, mechanically-explained improvement on held-out data
(+$0.38, strands 6→2), and it is **already implemented** in `DEF`.

**There is no legitimate shortest path to a bounded live validation right now,
and I am not going to manufacture one.** Defensive completion is a *taker* action.
Taker execution is unauthorized, and the deployed verifier enforces
`maker_only_forbids_defensive_crossing: true`. A live profitability test of a
mechanism measured negative out-of-sample would also be spending capital to
confirm a loss.

The shortest path that is actually available:

1. **Shadow-measure the defensive arm against live books.** The code exists; this
   needs observation only — no taker order, no new capability, no capital.
2. **Accumulate independent settlements.** Today's evidence is 48 held-out
   windows. The unit of independence is the settlement, and 48 is too few — that
   is precisely what the XRP05M dissolution demonstrates.
3. **Gate on the pre-committed band.** B must clear $50/day to earn dedicated
   engineering. It is currently at **−$2.40/day**.

**I am keeping this separate from the existing three-window authorization**, which
remains an execution-validation exercise for the maker lifecycle fix, unexpanded
and still blocked at its own step-2 prerequisite.

---

## What the data actually supports about $480/day

**Zero. The measured number is negative.**

Three structurally independent premises have now been measured:

| premise | result | evidence |
|---|---|---|
| locked / cross-venue arbitrage | **$0/day** certified | Aug census, 136.9M triggers |
| directional mispricing | **−$0.109/trade** over 47,609 signals | this work |
| inventory / pair management | **−$2.40/day** best arm | this work + 12 settled live windows |

The August verdict already concluded the aspiration "requires a different
strategy/economic premise." Two further premises have now been tested and both
are negative.

**And scale cannot fix it.** Matched pairs do return 11.81% on deployed capital
per window, and $480/day would need ~$42.33 of matched notional per window
against today's $1.72. But size multiplies the sign you have, and the sign is
negative — the residual losses exceed the pair profits at every size, venue,
series, queue depth and completion ceiling tested. **Scaling a negative
expectancy produces a larger loss, faster.**

What would change the answer is not a bigger bet but a missing measurement: a
**λ-resolved order-book tape**. Every fill conclusion here is capped by ~20s book
sampling, which is also why latency was unresolvable in the taker study. That is
a collector change, not new infrastructure, and it is the only cheap thing left
that could move a sign rather than a magnitude.
