# P6 — Duration selection across Gemini BTC 5m / 15m / 1h

**Status: MEASUREMENT AND OFFLINE REPLAY ONLY. No live orders, nothing armed,
no live change proposed.**

Source: Gemini's own public endpoints, read 2026-09-22. 1,000 settled events
paged from `/v1/prediction-markets/events?status=settled`, yielding **729
genuine BTC/ETH contracts** at 5m, 15m and 1h life, all spanning
**02:30-17:20 UTC today** so the durations are compared at the same times of
day. Per-contract tape from `/v1/trades/{symbol}`. 420 of 729 had at least one
print.

---

## 0. Item 3 first — hourly payoff semantics, because it changes what may be done

**The 1-hour BTC ladder is NESTED THRESHOLDS, not intervals.** The venue's own
contract text:

> "If the price of Bitcoin (BTC) is **$86,100 or above** on September 22, 2026
> at 2pm ET, this market will resolve to Yes. Otherwise, it will resolve to No."

and the ladder confirms it — YES asks fall monotonically and **sum to 4.81,
not 1.00**:

| strike | YES bid | YES ask | NO bid | NO ask | passive pair | crossing pair |
|---|---|---|---|---|---|---|
| >$86,100 | 0.85 | 0.93 | 0.07 | 0.15 | 0.92 | 1.08 |
| >$86,300 | 0.73 | 0.79 | 0.21 | 0.27 | 0.94 | 1.06 |
| >$86,500 | 0.50 | 0.55 | 0.45 | 0.50 | 0.95 | 1.05 |
| >$86,800 | 0.16 | 0.23 | 0.77 | 0.84 | 0.93 | 1.07 |

Three consequences:

1. **The mechanism transfers per contract.** Each strike is a genuine binary
   with a real YES/NO complement, so a matched pair *inside one strike* pays
   exactly $1 whatever BTC does. That part is separable and is reported
   per-strike below.
2. **Residual does NOT diversify across strikes — it stacks.** Since
   `>=86,100 ⊇ >=86,200 ⊇ ... ⊇ >=86,800`, eight residual positions are **one**
   BTC direction bet. Every residual figure below is therefore aggregated to
   the EVENT, and strike opportunities are never summed as independent
   capacity. A per-contract residual cap would silently permit up to 8x the
   intended exposure.
3. **The metadata is misleading.** The event carries `type: "categorical"` and
   calls itself an *"Interval BTC price prediction event"*, both suggesting
   mutually-exclusive buckets. `strike.type` is literally `"above"`, and the
   price ladder settles it. Taking the metadata at face value would have been
   the exact equivalence error the instruction warned against.

For contrast BTC15m is `type: binary`, `template: crypto-up-down`, one
contract per event, `strike: {type: "reference", ...}` fixed at window open.
Both families share `quantityMinimum 0.01` and `priceIncrement 0.01`.

---

## 1. The duration-normalized habitat

| lane | n | traded | life | prints | $ | prints/min | $/min | two-sided | gap | levels | amplitude |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC 5m | 178 | 159 (89%) | 5m | 4 | $22 | **0.800** | $4.35 | 0.33 | 20s | 3 | 0.27 |
| BTC 15m | 58 | 54 (93%) | 15m | 5 | $138 | 0.333 | **$9.21** | 0.33 | 67s | 4 | 0.27 |
| BTC 1h | 153 | 117 (76%) | 60m | 5 | $204 | 0.083 | $3.40 | 0.20 | 108s | 4 | 0.15 |
| ETH 5m | 177 | 40 (23%) | 5m | 2 | $8 | 0.400 | $1.56 | 0.00 | 6s | 2 | 0.05 |
| ETH 15m | 58 | 32 (55%) | 15m | 3 | $89 | 0.200 | $5.94 | 0.00 | 0s | 2 | 0.01 |
| ETH 1h | 105 | 18 (17%) | 60m | 2 | $79 | 0.033 | $1.32 | 0.10 | 29s | 2 | 0.01 |

**Longer duration does not compensate with resting time.** The explicit test:
BTC 1h has the *most* dollars per contract ($204) and the *least* flow per
minute (0.083/min, a print every 108 seconds), the weakest two-sidedness
(0.20) and the smallest price amplitude (0.15). A passive order living an hour
does not see more two-sided opportunity than four 15-minute orders do — it
sees less.

---

## 2. Item 1 — pair-cost opportunity

Share of tape-supported pairs by achievable cost. **This is the table P1 said
to build:** raw flow without attractive matching economics is not a habitat.

| lane | pairs | median pair | **<0.95** | 0.95-1.00 | **>1.00** |
|---|---|---|---|---|---|
| BTC 5m | 226 | 0.9800 | 29.6% | 54.0% | 16.4% |
| **BTC 15m** | 108 | **0.9600** | **45.4%** | 18.5% | 36.1% |
| BTC 1h | 202 | 0.9900 | 38.1% | 12.9% | **49.0%** |
| ETH 5m | 18 | 0.7000 | 55.6% | 27.8% | 16.7% |
| ETH 15m | 20 | 1.0300 | 15.0% | 15.0% | 70.0% |
| ETH 1h | 13 | 0.9800 | 38.5% | 53.8% | 7.7% |

**BTC15m offers the best cheap-pair share (45.4% below $0.95). BTC1h is the
worst: half its available pairs cost more than $1** — a guaranteed loss on the
leg that is supposed to be safe, which is precisely the failure mode P1 found
separating L5's losing windows.

## 3. Item 2 — residual-quality opportunity

Where sequential inventory would leave unpaired legs, by unit cost.

| lane | residual legs | **<0.35** | 0.35-0.50 | 0.50-0.65 | 0.65-0.80 | >0.80 |
|---|---|---|---|---|---|---|
| BTC 5m | 329 | 36.8% | 12.5% | 35.0% | 7.3% | 8.5% |
| **BTC 15m** | 115 | **52.2%** | 16.5% | 14.8% | 7.8% | 8.7% |
| BTC 1h | 262 | **33.6%** | 21.0% | 24.0% | 10.7% | 10.7% |

**The durations trade off exactly against each other.** BTC15m has the best
pair economics and the **worst** residual quality (52.2% in the band L5 loses
79% on — and note this independently reproduces the 46.1% measured in L5's own
BTC15m history). BTC1h has the worst pair economics and the **best** residual
quality. BTC5m is between on both. Neither dominates, which is the case where
selecting between durations beats committing to one.

---

## 4. Hourly: per-strike matched, event-level residual, and strike selection

14 BTC hourly events. Selection is **causal**: it sees only each strike's
FIRST observed print (price and time) and everything counted is strictly
later.

| selection | events | strikes used | pairs | pair cost | matched $ | residual legs/event | residual cost/event | residual cost total |
|---|---|---|---|---|---|---|---|---|
| ALL STRIKES | 14 | 8.0 | 202 | 1.0030 | **−0.61** | 16.5 | $8.16 | $117.03 |
| **nearest-money 1** | 14 | 1.0 | 43 | **0.9858** | **+0.61** | **1.0** | **$0.82** | **$17.76** |
| nearest-money 2 | 14 | 2.0 | 85 | 1.0004 | −0.03 | 4.0 | $1.71 | $32.21 |
| nearest-money 3 | 14 | 3.0 | 116 | 1.0069 | −0.80 | 7.0 | $3.38 | $50.31 |

**Selecting the single nearest-the-money strike is strictly better on every
axis**: it flips matched P&L from −$0.61 to +$0.61, brings pair cost below $1
(0.9858 vs 1.0030), and cuts event-level residual exposure **tenfold**
($0.82/event vs $8.16) while using one eighth of the capital. The
away-from-money strikes are where both the above-$1 pairs and the bulk of the
residual come from — wide spreads relative to a small price.

ETH 1h corroborates on 10 events: nearest-money-1 gives pair cost 0.8700 and
$0.26/event residual against 0.8923 and $0.86 across the ladder.

---

## 5. The selector: trade everything vs flow-gated vs completion-gated

**Leak-free by construction.** At the moment we must decide whether to enter
market *k*, market *k* has not printed at all — so gates may only read the
PRECEDING contract in the same series. A gate reading market *k*'s own
realized flow would be reading the answer.

Scored on **economic opportunity**, not trades/minute:

```
score = matched economics + residual economics + credited maker rebate - fees
```

Residual expectancy uses **L5's own band returns** (2,193 windows, monotone,
surviving BTC/ETH and 5m/15m splits) rather than a fitted number. **The rebate
term is ZERO throughout: no credited Gemini rate exists until tonight.**

| lane | arm | entered | pairs | pair cost | matched $ | residual cost | residual P&L | **NET $** | notional |
|---|---|---|---|---|---|---|---|---|---|
| BTC 5m | trade everything | 178 | 226 | 0.9346 | +14.77 | 148.43 | −32.82 | **−18.05** | 359.66 |
| BTC 5m | flow gate ≥0.5/min | 106 | 168 | 0.9325 | +11.34 | 103.46 | −21.28 | −9.94 | 260.12 |
| BTC 5m | completion ≥2 pairs | 58 | 115 | 0.9310 | +7.93 | 60.45 | −12.78 | −4.85 | 167.52 |
| BTC 5m | pair cap 0.97 | 178 | 138 | 0.7585 | +33.33 | 254.99 | −47.44 | −14.11 | 359.66 |
| **BTC 5m** | **completion ≥2 + cap 0.95** | 58 | 75 | 0.7345 | +19.91 | 112.43 | −18.09 | **+1.82** | 167.52 |
| BTC 15m | trade everything | 58 | 108 | 0.9564 | +4.71 | 44.41 | −12.89 | −8.18 | 147.70 |
| BTC 15m | **pair cap 0.97** | 58 | 79 | 0.8433 | +12.38 | 81.08 | −13.99 | **−1.61** | 147.70 |
| BTC 15m | completion ≥2 + cap 0.95 | 24 | 34 | 0.8282 | +5.84 | 35.09 | −8.50 | −2.66 | 63.25 |
| BTC 1h | trade everything (nearest-money) | 14 | 43 | 0.9858 | +0.61 | 17.76 | −2.23 | −1.62 | 60.15 |
| BTC 1h | pair cap 0.97 | 14 | 22 | 0.8755 | +2.74 | 40.89 | −5.68 | −2.94 | 60.15 |

Three things this says:

- **The pair cap is the dominant lever, not the flow gate.** Capping pair cost
  at 0.97 moves BTC15m from −8.18 to −1.61 and BTC5m matched from +14.77 to
  +33.33. Flow gating mostly just scales activity down proportionally.
- **But the cap raises residual**, because refusing an expensive pair leaves
  the first leg unpaired. BTC5m residual cost goes 148 -> 255. The cap and the
  residual work against each other, which is why only the **combination**
  (gate the market, then cap the pair) produces the single positive arm.
- **One arm of eighteen is positive**: BTC 5m, completion-gated with a 0.95
  pair cap, **+$1.82 on $167.52 of notional (+1.09%)**. On one day and 58
  entered markets that is a candidate, not a result.

---

## 6. What the rebate has to clear

Rebate per pair, and in basis points of notional, required to bring each arm
to zero:

| lane | arm | pairs | NET $ | rebate/pair needed | **bps of notional** |
|---|---|---|---|---|---|
| BTC 5m | trade everything | 226 | −18.05 | $0.0799 | 502 |
| BTC 5m | pair cap 0.97 | 138 | −14.11 | $0.1022 | 392 |
| **BTC 5m** | **completion ≥2 + cap 0.95** | 75 | **+1.82** | **already positive** | — |
| BTC 15m | trade everything | 108 | −8.18 | $0.0758 | 554 |
| **BTC 15m** | **pair cap 0.97** | 79 | −1.61 | **$0.0204** | **109** |
| BTC 15m | completion ≥2 + cap 0.95 | 34 | −2.66 | $0.0781 | 420 |
| BTC 1h | trade everything | 43 | −1.62 | $0.0376 | 269 |
| BTC 1h | pair cap 0.97 | 22 | −2.94 | $0.1334 | 488 |

**The decision-relevant number: BTC15m with a 0.97 pair cap needs only 109 bps
of credited rebate to break even** — the lowest requirement of any arm.
COHORT-2's own *accrual* implied 125 bps ($0.0833 on $6.67 of qualifying
volume). Gemini's published formula caps at 5% of notional (500 bps) over the
$0.20-$0.80 eligible band.

So tonight's credited figure is genuinely decisive: **if the realized rate
lands anywhere near the accrual, BTC15m-with-a-pair-cap crosses zero; if it
lands far below, no arm here clears.** Nothing above assumes any rebate.

---

## 7. Limits, stated plainly

- **These are TAPE-CAPACITY UPPER BOUNDS.** The pairing model counts what the
  tape would have permitted a participant who won every relevant print. P3
  measured us at 33-100% of the BTC15m tape, so for BTC15m the bound is close
  to achievable; for BTC5m and BTC1h we would be competing for the same
  prints and the realized figure would be lower.
- **One day.** 02:30-17:20 UTC on 2026-09-22. 178 / 58 / 153 contracts and 14
  hourly events. Nothing here establishes expectancy.
- **Residual expectancy is borrowed from L5's venue**, not measured on Gemini.
  It is the best available prior and it is applied identically to every arm,
  so the *ranking* is more robust than the levels.
- **No fees are modelled**, because no Gemini fee has ever been observed:
  every COHORT-2 fill reported `fee_amount` 0. If fees exist, every arm is
  worse.
- **No rebate is modelled.** Section 6 says what would be required.

## 8. Proposed next step — not a live experiment

Nothing here is proposed for arming. The evidence supports one narrow
follow-up once tonight's credited rebate is known:

- If the credited rate >= ~110 bps: the candidate worth shadow-replaying is
  **BTC15m with a pair-cost cap**, which is the lowest-requirement arm and
  also the lane where our fill share is already demonstrated.
- If the credited rate is materially lower: **no duration arm clears**, and
  the combination in section 5 (BTC5m, completion-gated, capped) is a one-day
  artifact that should be re-measured over more days before it is believed.

Either way the sequence is shadow first, and no live orders.
