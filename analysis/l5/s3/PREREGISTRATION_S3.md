# S3 preregistration — bounded passive liquidity, measurement only

**Frozen before implementation.** Spec fingerprint
`8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df`
(`analysis/l5/s3/spec.py`, hard-coded literal so `assert_frozen()` cannot be
vacuous). Every runner asserts it and aborts if a parameter moved.

**Zero capital. No order submission. No signing.** Frozen prospective S1
(`441c375e…b6fe`) is not modified and not imported for mutation.

## 0. What S3 is, and what it is forbidden to be

S3's purpose, in one sentence:

> Measure whether a bounded passive two-sided inventory process can obtain
> sufficient real prospective maker-fill opportunity, at acceptable
> acquisition economics, when our own order lifecycle and queue state are
> observable.

**S3 is not an L5 next-action predictor.** Historical BTC15m next-action
prediction returned lift **1.001** against persistence with S-PERSIST in
12/12 windows, and conditional future-inventory prediction returned
**E2_POSITIVE = False** at every horizon (best +2.6 %, −4.5 % at T+60,
against a ≥20 % bar). Those endpoints are **refuted, and must not be
resurrected through new feature fitting.** The spec records
`is_predictor: False`, `no_next_action_prediction: True` and
`no_conditional_inventory_forecast: True`, and S3 carries no L5-prediction
feature of any kind.

No placement or maintenance parameter was chosen by looking at settlement
P&L. Each one below carries an explicit provenance tag, and the tags are
machine-readable in the spec.

## 1. Reconciliation of S1 and S2 — what S3 inherits

| | S1 (frozen) | S2 (diagnostic) | what S3 takes |
| --- | --- | --- | --- |
| Stranding | **876 s untouched, 62 ticks behind, inert from 04:00** | eliminated: **0 stale side-ticks in 11/11 windows**, worst displacement 6 ticks | S2's maintenance, unchanged |
| Two-sided acquisition | matched fraction **0.800** | **0.947** vs L5's 0.953; both-side quoting 3,280 → 5,743 side-ticks | S2's quote maintenance restored this and the residual cap stayed at 80 |
| Acquisition price | pair cost 0.867 (flattered by inactivity) | **degrades monotonically 0.940 → 1.052** | **the failure S3 must close** |
| Economics | −5.58 % per dollar under the unbiased arm; the +8.06 % in arm A rests on 4 of 11 windows at 1/19th of L5's spend — noise | **−5.96 %** on ~4× the volume | neither policy had an edge; S3 measures opportunity, not P&L |

**The diagnosis.** S1 converted inactivity into *nothing*. S2 converted
inactivity into *adverse fills*: following the touch without a price ceiling
means every reprice re-offers at a worse level, so restoring activity bought
two-sided acquisition at the cost of acquisition price. Both arms of that
trade-off lose. S3's one substantive addition is the mechanism that refuses
both horns.

Also inherited as hard constraints:

- **Withdrawal is clock- and inventory-driven.** L5's withdrawal associates
  with TTE (Cliff's delta −0.82), accumulated same-side inventory (+0.74),
  matched inventory (+0.72) and clip exhaustion (+0.48), and with **no**
  contemporaneous book variable (|delta| ≤ 0.08 across spread, depth at
  touch, depth at our price, price). S3's withdrawal rule uses only the
  former.
- **Fill quantity reflects counterparty consumption.** 113 of 613 multi-fill
  clips sum to exactly 80.00. **≤80 is a latent clip ceiling, not proof every
  order is 80**: only 19.4 % of clips reach 80 and the median clip is 0.31 of
  it. S3's intended quantity is therefore **20 shares**, well below the
  ceiling, and the spec asserts `quantity_is_not_the_ceiling`.
- **The $1.00 atomic taker pair is unavailable on PMUS and must not be
  emulated.** No mint/split/merge primitive exists there; two separate taker
  orders are a different instrument carrying leg risk. The spec marks it
  `FORBIDDEN_NOT_AVAILABLE_ON_PMUS` and S3 is maker-only
  (`taker_permitted: False`).

## 2. Placement mechanics, from surviving evidence only

| parameter | value | provenance |
| --- | --- | --- |
| offset below touch | **0 ticks** (at the touch) | `L5_EVIDENCE` — median offset 0, 26 % exactly at the touch, n=1,926 |
| levels per side | 1 | `LEARNING_BOUND` — L5's ≥3 levels is a *lower bound* (cancels invisible); widening multiplies hypothetical fills without improving the evidence standard |
| intended quantity | **20 shares** | `LEARNING_BOUND`, explicitly not the 80 ceiling |
| both sides | yes | `L5_EVIDENCE` — 10/12 windows two-sided within 60 s |
| maker/post-only | required | design; `participateDontInitiate` + GTC is NATIVE on PMUS |
| taker | forbidden | — |

## 3. The reservation price — closing S2's failure mode

**Rule:** never place or reprice a side above
`max_combined_pair_cost (0.96)` minus the best achievable price on the
complement. If the touch moves above that, **stand down on that side rather
than follow it.**

Provenance is `PAIR_ECONOMICS` — the arithmetic of fee-safe pair completion
and the repo's existing ≤0.96 spring discipline. It is stated plainly that
**this is not an L5 behaviour**: L5's withdrawal shows no association with
book variables, so a price-triggered stand-down is *our* design choice. The
spec keeps `no_book_triggered_withdrawal: True` for the L5-derived rules and
records reservation stand-downs under a **separate reason code**, so the two
are never conflated in the output.

## 4. Maintenance

| trigger | threshold | provenance |
| --- | --- | --- |
| displacement | touch **0.06 in price** above us | `L5_EVIDENCE` — fill offset p99 = 6, max = 9 on Polymarket's **0.01** grid |
| staleness | below the touch > **69 s** | `L5_EVIDENCE` — L5's maximum absence from a token, taken as the max, not a percentile |
| fixed lifetime | **none** | a quiet book at the touch reprices zero times however old the order |

Both thresholds are at the **loose** end on purpose: a larger threshold
reprices *less* and therefore manufactures *fewer* hypothetical fills.

**The tick trap, recorded before implementation:** PMUS's crypto tick is
**0.005**, half Polymarket's **0.01**. "6 ticks" does **not** translate.
The threshold is therefore held **in price**, and per-market
`orderPriceMinTickSize` / `minimumTradeQty` are read from live venue metadata
and fail closed if absent.

Every reprice is a cancel/replace and **is never a fill**. Every reprice is
subject to the reservation.

## 5. Withdrawal

| trigger | value | provenance |
| --- | --- | --- |
| stop opening new interest | TTE ≤ **120 s** | `L5_EVIDENCE` — withdrawal-associated TTE median 102 s vs 485 s for continuation; 120 s is the loose side |
| clip exhaustion | withdraw when the clip is emptied | `L5_EVIDENCE` — median fraction consumed before withdrawal **1.00** vs 0.27 before a level move |
| inventory target reached | withdraw | `L5_EVIDENCE` |
| book state | **never a withdrawal trigger** | L5 shows no book association |

## 6. Instrumentation — every hypothetical owned order

One record per order, appended on every state change, never overwritten:

`order_id` · `rung_id` · `market_slug` · `outcome` · `placement_ts` ·
`price` · `intended_quantity` · `queue_ahead_estimate` ·
`queue_ahead_bounds` · `queue_ahead_observable` · `bbo_at_placement` ·
`full_depth_at_placement` · `depth_at_our_price` ·
`price_distance_from_touch` · `queue_depth_change_events[]` (each with ts,
depth before/after, our estimated position) · `maintenance_reason` ·
`replacement_reason` · `predecessor_order_id` · `fill_evidence_class` ·
`fill_evidence_detail` · `partial_fill_estimate` ·
`partial_fill_defensible` · `age_s` · `resting_time_s` ·
`inventory_up` · `inventory_down` · `matched` · `residual` ·
`withdrawal_reason` · `reservation_binding` · `spec_fingerprint` ·
`bbo_age_s`.

`queue_ahead_estimate` is an **estimate with stated bounds**. Depth is
anonymous, so what sits ahead of us is not directly observable; where it
cannot be bounded the record sets `queue_ahead_observable: False` rather
than inventing a priority.

## 7. Three ledgers and the evidence standard

Separate, never merged: **EVIDENCED_FILL** / **UNCERTAIN** / **NO_FILL**.

- **No touch = fill.** A price being reached is not evidence.
- **No invented queue priority.**
- **No hypothetical fill may become primary economics without independent
  execution evidence.** The spec asserts
  `hypothetical_fill_may_be_primary_economics: False`.
- Partial-fill estimates only where defensible, flagged per record.
- **The known bias is restated wherever these fills are quoted:** a
  level-consumed standard fires only when the touch crosses us, so it
  mechanically selects adverse fills. In S2 the less-biased arm raised fills
  63 → 83 and improved pair cost 1.0518 → 1.0347 — the bias is real, and it
  did **not** explain away the economics. S3 reports it as biased, never as
  unbiased.

## 8. Advancement metrics, predefined before collection

1. **M1** evidenced fills per market
2. **M2** two-sided evidenced acquisition (share of markets with evidenced
   fills on both outcomes)
3. **M3** matched fraction
4. **M4** residual distribution and max
5. **M5** acquisition pair cost
6. **M6** queue-ahead and resting-time distributions
7. **M7** markouts at **+1 / +5 / +10 / +30 / +60 s**
8. **M8** capital tied up
9. **M9** withdrawal behaviour (reason mix, TTE and inventory at withdrawal)
10. **M10** hypothetical fees and rewards, **separately accounted**

**Matched-pair economics and residual settlement economics are reported
separately** — never summed into one number.

**Rebates and rewards are not counted** unless actually attributable under
PMUS rules and actually credited. The spec sets `rebates_counted: False` and
`rewards_counted: False`. `realized` requires settlement.

## 9. Cohort size and the catastrophic-mismatch gate

**Cohort: 25 BTC15m markets**, contiguous, not cherry-picked. The cohort may
**not** be extended to reach a metric, and results may not be read before the
25th market closes — with one exception, the gate below.

**Catastrophic-mismatch gate**, evaluated every market. Its purpose is to
abort early if the *instrument is broken*, **not** to peek at economics.
Abort collection if any of:

| condition | threshold |
| --- | --- |
| orders never evidenced across the first N markets | N = 5 |
| markets with two-sided evidenced acquisition | < 20 % |
| share of records with queue-ahead unobservable | > 50 % |
| share of BBO staleness breaches | > 10 % |
| reservation violations | > 0 |
| any order submitted or signed | > 0 |

A gate breach **halts collection and is reported as a measurement failure.
It is never grounds for retuning a placement parameter and re-running.**

## 10. What a result can and cannot authorize

S3 answers exactly one question:

> If we explicitly maintain bounded passive liquidity rather than predict
> L5's next fill, does the current PMUS BTC15m market provide enough clean
> maker execution to recreate the economically useful part of L5's inventory
> formation?

A positive result authorizes **nothing beyond the next named stage of
review**. It is evidence about *opportunity and acquisition economics under
observable lifecycle*, not a profit estimate, not an edge, and not a reason
to raise any bound. A negative result is a complete answer and is to be
reported as one.
