# R5FT live run, 2026-09-13 — action-by-action forensic report (A–L)

Serving build `8606c87f`, deployment `718ca492-cd77-4095-a13a-b16c1533925c`,
rung R5FT. Read-only observation; nothing in production was altered while this
run was live. Raw tape and every analysis script are preserved beside this file
in `live_tape_2026-09-13/`.

**Accounting label for the whole document: submitted → filled → matched →
settled → realized. Nothing here is realized. Locked pair economics are NOT
realized P&L and are never added to one.**

---

## A. Exact action-by-action live tape

Every venue-confirmed FILL, in order, with running inventory. All maker,
`isAggressor: false`, post-only.

### `aec-atp-jayfri-maxbas-2026-09-13` (ATP, Jay Dylan Friend vs Max Basing)

```
13:01:43  open     YES @0.45 x2.22  rest  0.91s -> YES 2.22  NO 0  matched 0  resid +2.22
13:05:00  extend   YES @0.45 x2.22  rest  3.09s -> YES 4.44  NO 0  matched 0  resid +4.44
13:18:46  extend   YES @0.59 x1.69  rest 10.87s -> YES 6.13  NO 0  matched 0  resid +6.13
13:24:14  extend   YES @0.39 x2.45  rest 13.01s -> YES 8.58  NO 0  matched 0  resid +8.58
FINAL: YES 8.58 @ VWAP 0.460442, NO 0, spent $3.9506, matched 0
```

Ten COMPLETE orders submitted, **zero filled**. Last session 13:29:12; from
then the YES book was 0.18/0.19, so the NO complement cost ~0.81 against an
`economic_max_px` of 0.5396 and the ceiling had no legal quote. Residual frozen
to settlement.

### `atc-epl-cov-bha-2026-09-13-bha` (EPL, Coventry v Brighton)

```
13:31:37  open     YES @0.47 x2.00  rest  6.72s -> YES 2.00  NO 0.00  matched 0.00  resid +2.00
13:32:38  extend   YES @0.44 x2.27  rest 24.89s -> YES 4.27  NO 0.00  matched 0.00  resid +4.27
13:36:08  complete NO  @0.30 x1.81  rest 13.02s -> YES 4.27  NO 1.81  matched 1.81  resid +2.46
13:41:57  complete NO  @0.30 x1.44  rest  1.94s -> YES 4.27  NO 3.25  matched 3.25  resid +1.02
14:04:43  open     YES @0.71 x1.40  rest 31.92s -> YES 5.67  NO 3.25  matched 3.25  resid +2.42
14:08:02  complete NO  @0.29 x1.42  rest  2.08s -> YES 5.67  NO 4.67  matched 4.67  resid +1.00
14:18:52  complete NO  @0.01 x1.00  rest 19.84s -> YES 5.67  NO 5.67  matched 5.67  resid  0.00
FINAL: YES 5.67 @ 0.517248, NO 5.67 @ 0.246349, spent $4.3296, FULLY MATCHED
```

The 14:04:43 `open` on a market already holding 4.27 YES / 3.25 NO is the
section-F defect firing live.

### `asc-lal-lev-fcb-2026-09-13-pos-2pt5` (La Liga, over/under 2.5)

```
14:26:55  open     YES @0.39 x2.45  rest  6.97s -> YES 2.45  NO 0.00  matched 0.00  resid +2.45
14:26:55  extend   YES @0.39 x2.45  rest  5.34s -> YES 4.90  NO 0.00  matched 0.00  resid +4.90
14:26:55  complete NO  @0.57 x2.45  rest 27.56s -> YES 4.90  NO 2.45  matched 2.45  resid +2.45
14:29:02  extend   YES @0.39 x2.45  rest 14.36s -> YES 7.35  NO 2.45  matched 2.45  resid +4.90
14:29:02  complete NO  @0.57 x2.45  rest  5.29s -> YES 7.35  NO 4.90  matched 4.90  resid +2.45
14:34:19  complete NO  @0.53 x2.17  rest 11.25s -> YES 7.35  NO 7.07  matched 7.07  resid +0.28
FINAL: YES 7.35 @ 0.39, NO 7.07 @ 0.557722, spent $6.8096
```

First market to complete a pair inside a single session.

### `aec-setkamecz-snimic-kruluk-2026-09-13`

```
14:42:49  open     NO  @0.45 x1.88  rest  0.96s -> YES 0  NO 1.88  matched 0  resid -1.88
FINAL: NO 1.88 @ 0.45, spent $0.8460
```

First NO-side first leg of the run.

**Eleven markets were touched; four took fills.** The other seven produced
`SPRING_NO_FILL` or `SPRING_SKIP_NOT_QUALIFYING` with zero inventory.

---

## B. Matched-pair economics (locked, UNSETTLED)

`matched_qty = min(YES_qty, NO_qty)` per market. No average pair cost is ever
taken across unequal legs.

| market | matched | pair cost/unit | acquisition cost | guaranteed payout | locked |
|---|---|---|---|---|---|
| cov-bha | 5.67 | 0.763597 | $4.329599 | $5.67 | **+$1.340400** |
| lal-lev-fcb | 7.07 | 0.947722 | $6.700400 | $7.07 | **+$0.369600** |
| jayfri | 0 | — | — | — | — |
| snimic-kruluk | 0 | — | — | — | — |
| **total** | **12.74** | — | **$11.030** | **$12.74** | **+$1.710000** |

Locked means the two legs are held; it does **not** mean settled and does
**not** mean realized. Payout assumes the venue pays $1.00 per matched pair at
settlement, which has not yet been observed for any of these.

---

## C. Directional residual economics

| market | side | qty | cost basis (VWAP) | dollars | settlement result |
|---|---|---|---|---|---|
| jayfri | YES | 8.58 | 0.460442 | $3.9506 | market RESOLVED, account not reconciled |
| lal-lev-fcb | YES | 0.28 | 0.39 | $0.1092 | market OPEN |
| snimic-kruluk | NO | 1.88 | 0.45 | $0.8460 | market OPEN |
| cov-bha | — | 0.00 | — | $0.0000 | fully matched |
| **total** | | | | **$4.9058** | |

No predictive skill is assumed for any residual. Each pays its full quantity if
its side settles true and zero otherwise.

---

## D. Completion fill determinants

Deduped by order_id across the whole run.

```
open      65 submitted /  5 filled =  7.7%
extend    47 submitted /  6 filled = 12.8%
complete  61 submitted /  7 filled = 11.5%
```

Per market (submitted/filled):

| market | open | extend | complete |
|---|---|---|---|
| jayfri | 2/1 | 21/3 | **10/0** |
| cov-bha | 27/2 | 13/1 | 32/4 |
| lal-lev-fcb | 1/1 | 12/2 | 19/3 |
| snimic-kruluk | 3/1 | 1/0 | 0/0 |
| 7 others | 32/0 | 0/0 | 0/0 |

**Rest duration is the strongest single execution determinant.**

```
complete  FILLED rest: 1.94, 2.08, 5.29, 11.25, 13.02, 19.84, 27.56
          UNFILLED p50 2.13s; 30 of 54 unfilled ended under 3.0s
extend    FILLED rest: 3.09, 5.34, 10.87, 13.01, 14.36, 24.89
          UNFILLED p50 11.93s; 11 of 41 under 3.0s
open      FILLED rest: 0.91, 0.95, 6.72, 6.97, 31.92
```

COMPLETE end reasons: `repriced 20, deadline 18, rejected 16, filled 7`. Half of
all completes are killed by the touch-chase re-quote or a post-only race before
they can age.

**Distance from touch.** Convention: `opp_ask_authenticated == 1 − yes_bid`, so
`NO_bid = 1 − yes_ask` is the touch a NO maker BUY must join. Positive distance
= our quote behind the touch. In the first 16 completes: 3 at touch, 7 inside,
6 behind (median 0.03, max **0.28**). The 0.28 case (13:29:12) had NO touch
0.81 against `economic_max_px` 0.5396 — the ceiling clipped us 28c away from
executable, correctly, because pair-at-touch was 1.2604.

Against the hypothesis list: **E is primary, B is the mechanism that converts E
into a stranded residual, D is real and independent, A contributes, C is not
binding (maker commission 0 bps on every order observed), F is secondary
(median 2 ticks behind L5's own chosen price).**

---

## E. Adverse-selection evidence

The two markets are a natural experiment with opposite first-leg drift.

**jayfri — drift against us.** YES `0.45 → 0.42 → 0.57 → 0.58 → 0.50 → 0.42 →
0.24 → 0.18`. Every YES fill was followed by a fall. Filled extends sat at
signed distance −0.03, −0.01, +0.03, −0.03 — i.e. we were mostly bidding *above*
the touch when hit. As YES falls the NO complement gets more expensive, pair at
touch exceeds 1.00, the ceiling clips the quote, and completion becomes
impossible. **Completion: 0/10.**

**cov-bha — drift for us.** YES `0.47 → 0.69 → 0.99`. NO became cheap (touch
0.31 → 0.01), our quotes sat at or inside it, pair cost 0.763597, and the book
completed to zero residual. **Completion: 4/32, including the final unit at NO
0.01.**

Completion is not uniformly broken. It fails **conditional on adverse first-leg
drift** — exactly when completing would cost more than the $1.00 the pair pays.

---

## F. State-key `flat` investigation

`src/polymarket_l5/action_model.py:74`:

```python
def state_key(last_price, tte_s, residual_qty):
    return "|".join((_bucket(last_price, PRICE_BANDS),
                     _bucket(tte_s, TTE_BUCKETS),
                     _bucket(abs(residual_qty), IMBALANCE_BUCKETS)))

IMBALANCE_BUCKETS = ((1.0,"flat"), (10.0,"imb1-10"), (50.0,"imb10-50"), (1e12,"imb>50"))
```

Matched inventory, absolute inventory, spend, residual dollars and pair cost
**never enter the state**. Only `abs(residual)`, unnormalized.

The live `flat` reading did not come from that threshold directly. It came from
`pmus_spring_l5_session.py:348 record_behavioral_shares()`, which adds L5's
**target p50 for the state|relation — once per fill, ignoring the contracts
actually filled**:

```
#  6  open     fill 2.00 YES  -> sharesB  10.0
#  8  extend   fill 2.27 YES  -> sharesB  20.0
# 21  complete fill 1.81 NO   -> sharesB  10.0
# 46  complete fill 1.44 NO   -> sharesB   0.0   -> |0| < 1.0 -> flat
```

Four fills of 2.00, 2.27, 1.81 and 1.44 contracts each contributed ±10. The
executable book was 1.02 contracts long.

The bucket is a **cliff**, not a gradient:

```
p25-45c|tte>10m|flat     n=  83   open 100%
p25-45c|tte>10m|imb1-10  n= 366   open   0%   (complete/extend only)
```

The identical failure class is documented in that same function for the
2026-09-07 R4 incident. The fix applied then (`if up == dn: reset`) catches only
the exactly-balanced book; cov-bha is the uncaught half.

---

## G. Quarantine / concurrency vs historical L5

Quarantine predicate (`pmus_residual_quarantine.eligibility`): `zero_resting_orders`,
`venue_confirmed`, `has_inventory`, `stays_in_held`, `stays_settlement_monitored`.
**Nothing looks at unmatched dollars or imbalance — "dormant" means quiescent,
not flat.** jayfri satisfied all five at 13:31 while holding 8.58 unmatched YES.

The scheduler then alternates (`pmus_l5_continuous_session.py:3070`):

```python
open_cycle = capacity and (_hold_rr["i"] % 2 == 0)
# capacity = len(acquiring) < concurrency and not allowance_spent
# acquiring = [s for s in held if s not in dormant]
```

With every held market dormant, `acquiring == []`, `capacity` is True, and every
other tick looks for a new market. That is how cov-bha was acquired at 13:31 and
how seven further markets were entered.

**Sequential test on L5's own canonical history** (2216 windows): for the first
trade of each window B, find windows A that started earlier, had not settled,
and carried a nonzero residual as of their last trade ≤ T_B.

```
opens while >=1 unrelated unsettled window held an earlier unmatched residual:
   1604 / 2216 = 72.4%
concurrent residual-bearing markets: p50 1, p75 2, p90 2, max 3
residual DOLLARS of those positions (n=2322):
   p25 $14.80  p50 $39.79  p75 $84.17  p90 $141.98  max $347.88  mean $58.49
```

**L5 does this routinely, and bounds it at 3.** The concept is behaviourally
supported. Two of the four quarantine conditions — zero resting orders,
venue-confirmed inventory — are **not observable** in public activity data and
remain untested against L5.

Observability defect: `concurrency: 1` is an *acquisition-slot* count and was
being reported as though it described exposure.

---

## H. Rewards and rebates

**None credited. None claimed. Zero.** Maker commission observed on every order
is 0 bps (`commissionsBasisPoints: 0`, `makerCommissionsBasisPoints: 0`). The
historically measured maker rebate of $0.0134 per credited fill is **not**
applied anywhere in this report and must not be until a venue credit is seen.

---

## I. Exact realized cashflow

**$0.00.**

- `aec-atp-jayfri-maxbas-2026-09-13` — `MARKET_STATUS_RESOLVED`, `closed: true`,
  `outcomes ["Jay Dylan Friend","Max Basing"]`, `outcomePrices ["0","1"]`; our
  side was Jay Dylan Friend. Position left the authenticated held set with **no
  settlement row**. The probe's own alarm fired at 13:57:17Z:
  `held_absent_while_risk_bearing … terminal_settlement_recorded: false`.
  Label: **resolved / not account-reconciled / not realized.**
- `atc-epl-cov-bha-2026-09-13-bha` — `MARKET_STATUS_OPEN`.
- `asc-lal-lev-fcb-2026-09-13-pos-2pt5` — `MARKET_STATUS_OPEN`.
- `aec-setkamecz-snimic-kruluk-2026-09-13` — open.

No `POSITION_RESOLUTION` row exists for any market in this run.

---

## J. L5 vs clone behavioural divergences

1. **Inventory representation.** L5's residual is executable shares; the clone's
   is netted p50 targets. 34.1% of live decisions diverge (section K).
2. **Fresh-open semantics.** L5 starts fresh only at exactly zero residual
   (100% of n=209); at 0.1–1.0 residual it is ~50/50 extend/complete and **0%
   fresh** (n=363). The clone emitted 30 OPENs from a 1.00–1.02 contract residual.
3. **Inventory sensitivity.** L5's fresh-buy share collapses from ~56% to **5%**
   once matched > 100 shares or spend > $100. The clone's state cannot see either.
4. **Scale.** L5 opens while carrying a median $39.79 residual; we carried $3.95.
5. **Completion price.** Median 2 ticks behind L5's own chosen price
   (`median_delta_ticks_touch_minus_executed: 2.0`); the opposite ask reaches
   L5's touch in only **0.87%** of orders.
6. **Size.** Every action is capped to ~$1.00 notional and labelled in-record as
   "owner size cap $30.00/market + venue minimum; declared safety override, not
   L5 behavior". L5's p50 is 10–11 shares / $5–10.
7. **State price convention.** Post-fill the key uses the outcome price of the
   last own fill, so after a NO complete at 0.30 the state reads `p25-45c` while
   the next order buys YES at 0.71.

---

## K. Behavioural-share adapter divergence

Live leg, n = 138 decisions where both scales are computable. Faithful scale =
`executable_residual_contracts × (p50_shares / behavioural_unit_contracts)`.

| divergence class | count | share |
|---|---|---|
| **sign** differs | 46 | **33.3%** |
| **zero-vs-nonzero** (behavioural flat, book not) | **34** | **24.6%** |
| **bucket** differs | 35 | 25.4% |
| any of the three | 47 | 34.1% |

The dangerous class: all 34 on cov-bha (34 of its 79 decisions), behavioural
residual **exactly 0.0** in every one, executable residual 1.00–1.02 contracts
with 3.25–4.67 matched alongside. Faithful bucket is `imb1-10` in **all 34**,
where model open probability is **0**. The clone acted `open` in **30**.

```
#46  YES 4.27 NO 3.25 matched 3.25 resid +1.02c -> behav +0.0sh (flat) vs faithful +5.1sh (imb1-10)
#78  YES 5.67 NO 4.67 matched 4.67 resid +1.00c -> behav +0.0sh (flat) vs faithful +5.0sh (imb1-10)
```

**Two distinct failure modes, one root cause:**

1. **Exact cancellation → spurious `flat`.** Requires equal p50 on both sides;
   cov-bha had p50 = 10.0 on every fill. Produces the 100%-OPEN cliff.
2. **Sign inversion → wrong extend/complete labelling.** Present even without
   exact cancellation: lal-lev-fcb #18 reads book **+0.28 contracts (long YES)**
   vs behavioural **−2.76 shares (short)**. 8 of its 25 decisions are
   sign-inverted. `_relation()` decides extend vs complete from that sign.

Root cause in both: `record_behavioral_shares()` adds ±p50 per fill regardless
of filled quantity.

---

## L. Unit economics and capacity, from actual fills only

```
capital deployed (spent, all markets)          $11.9358
  jayfri $3.9506 | cov-bha $4.3296 | lal-lev-fcb $6.8096 | snimic $0.8460
     (note: cov-bha and lal-lev-fcb spend includes both legs)

matched pairs                                  12.74 units
  acquisition cost                             $11.0300
  guaranteed payout at settlement              $12.7400
  LOCKED pre-settlement economics              +$1.710000   (UNSETTLED)

directional residual                           $4.9058 at cost
peak unmatched exposure (single market)        $3.9506  (jayfri, 13:24–)
peak portfolio unmatched                       $5.8894  (13:32, jayfri+cov-bha)
portfolio worst case now                       $4.9058

fees                                           $0.00 (0 bps maker, observed)
rebates / rewards actually credited            $0.00
REALIZED NET                                   $0.00
```

**Completion rate conditional on first-leg drift:**

| first-leg drift | market | complete submitted | filled | rate |
|---|---|---|---|---|
| adverse (YES 0.59→0.18) | jayfri | 10 | 0 | **0%** |
| favourable (YES 0.47→0.99) | cov-bha | 32 | 4 | 12.5% |
| mixed | lal-lev-fcb | 19 | 3 | 15.8% |

No $/day extrapolation is offered. Four fill-bearing markets, zero settled, is
not a basis for a rate.

---

## Defect register arising from this run

| id | defect | evidence | status |
|---|---|---|---|
| P0.3 | behavioural inventory mapping loses quantity, sign and zero | K: 46/138 sign, 34/138 false-flat | to implement |
| P1 | resolved market leaves held with no terminal reconciliation | jayfri, legacy cricket | to implement |
| P0.2 | refused REARM never re-checks | committed `8afe1997`, tested, undeployed | to integrate |
| obs | acquisition-slot concurrency reported as exposure | G | to fix with P0.3 |

