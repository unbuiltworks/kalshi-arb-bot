# L5's sequential inventory economics — where the money actually comes from

Read-only reconstruction over L5's own public BTC15m history: **767,996**
unique rows, **689,999** BTC15m, **5,566** windows, **684,275** trades and
**5,724** REDEEMs. Scripts in `analysis/l5/stage_c/`.

## 0. The methodological correction that governs every number below

`realized = redeemed - cost` is only meaningful when every trade that built
the position is visible. **It is not.** The public activity feed is paginated
and lossy: in **59.8%** of windows L5 redeemed *more* than the visible trades
could have bought, in the worst cases about twice as much.

Taken at face value the full set gives **+$62.71 per window = $6,020/day**,
against a known target of ~$480/day — a **12.5x overstatement**, and the
decomposition misses by **94%** (matched + residual = +$19,414 against a
claimed +$347,960). That is the input being wrong, not L5 being twelve times
better than believed.

So everything below uses only the **1,914 windows (34.5%)** where redeemed
equals the reconstructed winning-side holding **within 1%**. On those the
decomposition closes to **2.0%** (+$17,394 computed vs +$17,055 realized).

## 1. The reconciled economics

| | |
|---|---|
| windows | 1,914 |
| cash committed | $1,661,847 |
| **realized net** | **+$17,054.55** |
| per window | **+$8.9104** |
| implied at 96 windows/day | **+$855.40** |
| profitable windows | 1,086 of 1,914 (56.7%) |

Same order as the $480/day target, which is the sanity check the unfiltered
set failed.

## 2. The decomposition: a matched edge, partly given back by a residual drag

| component | total | per contract |
|---|---|---|
| matched inventory P&L | **+$53,014** | **+$0.0337** / matched contract |
| directional residual | **−$35,620** | **−$0.0937** / residual contract |
| net | +$17,394 | **+$0.0108** / matched contract |

Residual volume runs at **24.2%** of matched, so the drag costs
`0.0937 x 0.242 = 0.0227` per matched contract against a `0.0337` edge.

**L5 nets about 1.1 cents per matched contract**, and the residual is a coin
flip it loses more often than not: it won **42.2%** of windows, +$92,393 when
it won against −$240,951 when it lost. This is the measured confirmation of
`L5_OPERATING_MODEL.md`'s rule that residual exposure has no predictive
skill — for L5 it is a cost, not a source of return.

## 3. The edge IS the pair discount, and it changes sign at 0.98

Net per **matched** contract, by realised pair cost:

| pair cost | windows | matched q | mat/ctr | res/ctr | **net/ctr** |
|---|---|---|---|---|---|
| < 0.900 | 245 | 159,756 | +0.13804 | −0.18323 | **+0.06077** |
| 0.900–0.940 | 323 | 261,234 | +0.07646 | −0.14413 | **+0.03434** |
| 0.940–0.960 | 277 | 247,886 | +0.04995 | −0.06241 | **+0.03603** |
| 0.960–0.967 | 102 | 98,666 | +0.03612 | −0.09838 | **+0.01744** |
| 0.967–0.980 | 190 | 189,513 | +0.02689 | −0.07016 | **+0.01297** |
| **0.980–1.000** | 272 | 249,678 | +0.00987 | −0.05725 | **−0.00202** |
| 1.000–1.020 | 181 | 161,623 | −0.00932 | −0.00602 | **−0.01034** |
| >= 1.020 | 305 | 203,548 | −0.05411 | −0.03741 | **−0.06214** |

**The sign flips at ~0.98 — not at 1.00.** A pair costing 0.99 is "below a
dollar" and still loses money, because the residual that accompanies it costs
more than the one-cent pair discount earns.

What a creation ceiling would have captured:

| ceiling | windows | matched q | total net | net/ctr | volume kept |
|---|---|---|---|---|---|
| <= 0.940 | 568 | 420,990 | +$18,679 | +0.04437 | 26.8% |
| <= 0.960 | 845 | 668,876 | +$27,610 | +0.04128 | 42.6% |
| <= 0.967 | 947 | 767,541 | +$29,330 | +0.03821 | 48.8% |
| **<= 0.980** | **1,137** | **957,054** | **+$31,788** | **+0.03321** | **60.9%** |
| <= 1.000 | 1,409 | 1,206,733 | +$31,283 | +0.02592 | 76.8% |
| <= 1.020 | 1,590 | 1,368,355 | +$29,612 | +0.02164 | 87.1% |
| none | 1,895 | 1,571,903 | +$16,964 | +0.01079 | 100% |

**Total net is maximised at a 0.98 ceiling: +$31,788 against +$16,964
unceilinged — 1.87x — while still keeping 60.9% of matched volume.**

## 4. Frequency scales the edge; it does not create it

Segmented by transactions per window:

| trades | windows | mat/ctr | res/ctr | **net/ctr** | pair cost | residual share |
|---|---|---|---|---|---|---|
| 1–20 | 87 | +0.00150 | +0.03586 | +0.03298 | 0.9906 | **87.8%** |
| 21–50 | 351 | +0.03323 | −0.04035 | +0.01531 | 0.9611 | 44.4% |
| 51–100 | 753 | +0.03032 | −0.07391 | +0.00810 | 0.9671 | 30.1% |
| 101–150 | 410 | +0.03490 | −0.10035 | +0.01193 | 0.9672 | 22.9% |
| 151–250 | 269 | +0.03655 | −0.13721 | +0.01354 | 0.9664 | 16.8% |
| 251+ | 44 | +0.03346 | −0.19273 | +0.00507 | 0.9717 | 14.7% |

**Pearson r(transactions, net per matched contract) = −0.0425.** Essentially
zero. Frequency buys **more matched contracts at the same rate**, and brings
proportionally more exposure with them. It is a scaling lever, not an edge
lever — so **the Stage B "frequency is the blocker" conclusion was too
strong, and this supersedes it.**

Two details that matter more than the headline:

- The **1–20 bucket has no matched edge at all** (+0.0015/contract) and an
  **87.8%** residual share; its positive net is directional luck
  (res/ctr **+0.036**, the only positive residual in the table). **That is
  our engine's regime.** L5 has no edge there either.
- The matched edge only appears once pair cost falls from **0.9906** (1–20
  trades) to **0.961–0.967** (21+). Frequency's real function is to let the
  pair be **averaged down**, not to add trades.

Imbalance direction: a median **44.4%** of L5's trades reduce imbalance, so
**55.6% extend it** — same-side EXTEND and opposite-side COMPLETE are both
first-class. Peak unmatched runs at a median **0.376** of the final matched
book (p90 0.943), so L5 tolerates substantial imbalance but bounds it
relative to the matched book it is building.

## 5. What transfers to PMUS, and what does not

**TRANSFERS — the maker-built pair, fee-free.** L5's rows carry no fee at all
(`usdcSize == size * price` exactly), and PMUS charges **no maker
commission** (`actual_fees_usd: 0` on every Stage A window, 20 maker orders).
So a pair built from maker fills on PMUS pays what L5 pays: nothing. The
+$0.0337/contract matched edge is transferable **in principle**.

**DOES NOT TRANSFER — anything needing taker flow.** PMUS taker theta is
**0.0695**, roughly **$0.02/contract** at mid prices. Against a
+0.033/contract edge that is **~60% of it**, so an edge-motivated pair must
never be completed by crossing. (This is consistent with, and independent of,
the Stage B finding that crossing is fee-negative in expectation.)

**NOT CONFIRMED ON EITHER SIDE — incentives.** L5 may receive Polymarket
liquidity rewards that are invisible in this record; our own maker rebate is
estimated at theta 0.0125 and has been **credited $0**
(`UNVERIFIED_NO_VENUE_CREDIT_RECORD`). Neither is counted here, and neither
should be.

**NOT THE SPREAD.** Our PMUS books showed a 0.01 spread at 7 of 8 Stage A
fills — tight. The gap is not liquidity; it is that L5 averages its pair down
across ~105 maker fills on both sides while we take a single point-in-time
pair.

## 6. The largest transferable gap, measured on our own pairs

Our 9 created pairs (1415z, 1430z, 2115z CONFIRMED records) scored at L5's
measured per-bucket rates:

| | ours | L5 |
|---|---|---|
| median created pair cost | **0.9900** | **0.9671** |
| pairs <= 0.967 (profitable zone) | **2 of 9** | 48.8% of volume |
| pairs >= 1.000 (clearly negative) | **4 of 9** | 21.4% of volume |
| implied net per matched contract | **−$0.00187** | +$0.01079 (all), **+$0.03321** (<=0.98) |

**Our created pairs are, at L5's own rates, net negative.** A 2.3-cent gap in
pair cost at creation is the entire difference between +0.033/contract and
−0.002/contract.

And our recorded PMUS books confirm the mechanism: at the moment of a
first-leg fill the achievable 2-tick maker pair was 0.96–1.02, **median 0.99
at 8 of 8 fills** — squarely in the net-negative band.

## 7. The one decision rule, and how to test it

**Rule: the expected-value-motivated pair-creation ceiling is 0.98, not 1.00.
A pair created above 0.98 is defensive — variance-motivated — and must be
accounted as such, never as edge.**

Out-of-sample support (L5's 1,914 reconciled windows, settled on the venue's
own redemptions): net/contract **+0.01079 -> +0.03321 (3.1x)**, total
**+$16,964 -> +$31,788 (1.87x)**, keeping **60.9%** of matched volume.

Why this rule and not another:

- **It only ever refuses.** It cannot manufacture a fill that did not happen,
  so it is immune to the fill-probability uncertainty that makes every
  "quote here instead" rule unfalsifiable on a venue with no print endpoint.
- **It raises no limit.** No change to `max_live_orders`, opening size, round
  allocations or capital.
- **It corrects a real mislabel.** The engine currently splits
  `pair_edge_usd` (<=1.00) from `defensive_loss_usd` (>1.00). The evidence
  puts the line at **0.98**, so pairs in 0.98–1.00 are being counted as edge
  when they are worth **−0.002/contract**. Three of our nine pairs sit in or
  above that band.
- **It does not contradict the completion-gate correction.** Completing
  already-held inventory up to 1.00 remains right as *risk reduction* — Stage
  B measured that as fee-negative but variance-collapsing. The rule governs
  pair **creation**, and demands the two be reported separately.

**The honest tension:** applied to our 9 pairs the rule keeps 4 (0.92, 0.94,
0.97, 0.98) and refuses 5. It improves the rate on an already tiny matched
volume. **It does not solve volume, and nothing in this analysis does** —
frequency demonstrably does not change the rate, so volume remains an open
question rather than a solved one.

## 8. Next bounded validation experiment

Measure, on the existing 24/7 shadow stream with no capital and no new
service: **what fraction of pairs the engine would create clears a 0.98
creation ceiling, and what the refused ones would have cost.**

- Inputs already emitted: modelled two-sided inventory, the authenticated
  book, and the venue's own resolution.
- Success criterion: the kept pairs show a positive modelled net per matched
  contract **and** the refused pairs show a non-positive one — i.e. the
  ceiling discriminates on our books, not just on L5's.
- Failure criterion that must be reported, not explained away: if the ceiling
  refuses essentially all of our pairs, then our opening policy cannot reach
  the profitable band at all, and the pair-cost line is not the binding
  problem.
- No limit, cap, size, crossing or capital change is proposed.

## 9. What is NOT established

- **65.5% of L5's windows are unusable** for realized economics and are
  excluded. The reconciled subset may not be representative — windows whose
  history is complete may be quieter windows.
- These are L5's **fills**, not its quotes. Nothing here says where it rested
  an order, and fill probability remains unmeasurable on both venues.
- The per-bucket rates are **associations across windows**, not a causal
  claim that forcing a pair cost would produce that rate.
- No rebate or incentive is counted on either side; L5's Polymarket rewards,
  if any, are invisible here and could account for part of its margin.
- Nothing here is realized P&L for us. No order was submitted.
