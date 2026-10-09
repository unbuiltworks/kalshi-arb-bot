# L5 ECONOMIC DECISION — measured, 2026-10-09

All figures recomputed from `data/l5/raw/activity.jsonl` +
`activity_gap.jsonl.gz` with the conflict-free dedupe key
`(txh, conditionId, outcomeIndex, timestamp, price, size, type)`.
Nothing here is our realized profit. L5's incentives are not evidence
that equivalent incentives accrue to us.

## 1. Incentive reconciliation — the two figures were never in conflict

| | records | total |
|---|---|---|
| MAKER_REBATE | 110 | $35,228.32 |
| TAKER_REBATE | 73 | $7,525.73 |
| REWARD | 20 | $28,797.69 |
| **whole corpus** | **203** | **$71,551.74** |

By month: Apr $917.88 · May $2,003.87 · Jun $1,834.42 · Jul $5,773.14 ·
**Aug $61,022.43**. Sum = $71,551.74.

**$61,022 is August alone; $71,552 is Mar–Aug.** Independent recount
reproduces the canonical doc to the cent. REWARD exists ONLY in August
(20 records). TAKER_REBATE begins June. MAKER_REBATE begins April.

## 2. Trading cashflow, ex incentives (redeem − buy, by record month)

| month | buys | buy notional | redeems | redeem cash | trading net |
|---|---|---|---|---|---|
| 03 | 1,929 | $7,789 | 73 | $5,958 | −$1,831 |
| 04 | 34,223 | $312,961 | 711 | $317,732 | +$4,771 |
| 05 | 89,095 | $711,998 | 917 | $713,897 | +$1,899 |
| 06 | 77,097 | $671,143 | 1,020 | $677,727 | +$6,584 |
| 07 | 133,996 | $1,287,304 | 3,353 | $1,294,865 | +$7,561 |
| 08 | 424,475 | **$4,614,140** | 9,655 | $4,613,603 | **−$537** |
| **total** | 760,815 | $7,605,336 | 15,729 | $7,623,782 | **+$18,446** |

Mar–Jul gross buy notional **$2,991,196** — reproduces the doc's $2.99M.
Whole-corpus trading net +$18,446 vs the doc's event-attributed $18,536
(0.5%; redemptions lag trades across month boundaries).

**56% of L5's lifetime notional traded in August and produced nothing.**
Return on notional: Apr 1.52% → May 0.27% → Jun 0.98% → Jul 0.59% →
**Aug −0.012%**.

## 3. Venue toxicity — MEASURED, and it answers the 52× problem

L5 own-fill markout, BUY-only tape, price change after L5's own fill
(mean $/share):

| era | next print | t+10s | t+30s | t+60s | t+120s |
|---|---|---|---|---|---|
| early (pre 06-23) | −0.00003 | −0.00313 | −0.00294 | −0.00274 | −0.00039 |
| late | −0.00026 | −0.00337 | **−0.00381** | −0.00366 | −0.00270 |

n = 158,518 (early t+30s), 500,865 (late t+30s).

| | per contract |
|---|---|
| Polymarket own-fill markout, late t+30s | **−0.00381** |
| L5 all-in incentive rate | **+0.004554** |
| **net** | **+0.00074** |
| Gemini maker toxicity (prior measurement) | −0.238 (**62× worse**) |

**The canonical doc's inference is confirmed by measurement: L5's venue
has adverse selection ~62× lower than Gemini's, and its incentive rate
just covers it.** The rebate is not the edge; the low-toxicity book is.

CAVEATS, material: this samples price at L5's OWN fill times, not a book
mid, so it carries L5's selection and its own impact. It is BUY-only.
Median at t+30/60/120 is −0.01 in both eras — half of fills see at least
a one-cent adverse move. It is Polymarket, not Polymarket US.

## 4. Pair cost below $1.00 — routine, not exceptional

Per-event VWAP of both legs, 12,977 two-sided events (76.1% of 17,046):

| | early (n=2,034) | late (n=10,943) |
|---|---|---|
| median pair cost | **0.9581** | 0.9702 |
| p10 / p90 | 0.8778 / 1.0366 | 0.7800 / 1.1986 |
| **below $1.00** | **77.9%** | **63.2%** |
| below $0.98 | 65.8% | 54.4% |
| below $0.96 (our gate) | **51.3%** | 45.0% |
| matched shares/event median | **619.3** | 120.0 |
| leg balance (min/max) median | 0.827 | 0.689 |

Against Gemini **0 of 24,520** and Kalshi **0 of 2,269**. Our own three
PMUS matched pairs (22:00z, 00:00z ×2) sit inside this distribution.

## 5. Where the money actually goes

Early regime: 21.2 two-sided events/day × 619.3 matched shares ×
(1 − 0.9581) = **$550/day of gross matched-pair edge**.
Realized early trading, ex incentives: **~$144/day**.

**The directional residual destroys ~74% of the gross matched-pair edge.**
That, not venue or entry price, is the binding constraint.

## 6. On the $480/day target

It is NOT ruled out by this data, and the earlier framing was too strong.
$550/day of gross matched-pair edge is already present in L5's early tape
at L5's size. $480/day is approximately "capture the gross pair edge
without giving it back on residual." The target is a residual-control
problem, not an edge-existence problem.

What IS ruled out: reaching it by scaling frequency. $/event decays
8.48 → 4.27 → 4.27 → 2.03 → −0.17 while frequency scaled ~120×.
