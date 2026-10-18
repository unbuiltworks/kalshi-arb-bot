# The largest discrepancy is realized fee economics, not behaviour

Read-only measurement plus one refuse-only gate. No order, service,
deployment, limit or latch was touched. `L5_SHADOW_ONLY` stayed set
throughout and the HALT/watchdog infrastructure is unchanged.

## 0. The two numbers

| | value | source |
|---|---|---|
| L5's entire edge | **0.2935% of cost** | `data/l5_master/btc15m_sequences.jsonl`, recomputed |
| our realized fee load | **1.401% of cost** | `data/l5/ops/cost_basis_inspect_20261016T1658Z.json`, 47 venue-confirmed legs |

Our fees run **4.8x the whole edge** being harvested. L5's series carries
**zero** fee rows of any kind across 696,860 fills. This is not a fidelity
gap and no amount of behavioural replication closes it.

Of the 38 fee-bearing legs the MEDIAN fee is **3.67%** of leg cost and the
worst is **7.22%** on a $0.2772 leg.

## 1. A methodological result that constrains every future measurement

Reconstructing L5's cost as `sum(price * qty)` understates the corpus
`cost` field by **$27,549.93 = 0.434% of turnover**, across 4,904 of 5,546
windows. **The edge being measured is 0.2935%.** So the naive
reconstruction's error EXCEEDS the entire effect, and any verdict drawn
from it is noise.

Calibrating each window's legs pro-rata to the corpus `cost` reproduces the
record: net **+$18,717.24** against the corpus's **+$19,176.71**, the $459
residual being the known redemption mismatch (0.0072% of turnover).

**Rule this establishes: any model of this strategy whose cost accounting
is off by more than ~0.3% cannot tell profit from loss.**

## 2. Re-test of the one correction said to survive a holdout split

L5_CANONICAL 88.1#3 reports the 0.35 average-cost floor as "positive on
both sides with no sign flip". Independently recomputed on the calibrated
basis, threshold PRE-SPECIFIED at 0.35, temporal 70/30 split:

| variant | DEV | HOLDOUT |
|---|---|---|
| **drag** (avg pulled from >=0.35 to <0.35) | **-$2,520.77** | +$10,735.83 |
| **floor** (any fill leaving avg < 0.35) | +$4,035.82 | +$2,111.92 |

- the **floor** variant replicates: +0.069pp of holdout margin, matching the
  documented -0.070pp;
- the **drag** variant does NOT: it is **negative on DEV**, so the "no sign
  flip" claim is refuted for it;
- on DEV the floor is positive only in a narrow 0.25-0.35 band and negative
  at 0.20 and at >=0.40. A rule whose sign turns on a 5-cent threshold is
  fragile, and no threshold here was chosen by scanning outcomes.

At our authorized size the floor is worth ~**$0.003/window**. It is real and
it is not a path to the objective.

## 3. Why the pair trade's sign is set by cent rounding

The fee is `Theta * C * p * (1-p)` **rounded UP to the cent**, and our own
records show it is charged **per FILL**, not per order
(`cpcxrp-...-0330z|UP`: five fills, $0.0500 -- a cent each).

Verified exactly against a single-fill leg:
`cpc-btc-updown-15m-2026-10-15-2230z|DOWN`, cost 0.2921 in one fill,
charged $0.0200, and `0.0695 * 1 * 0.2921 * 0.7079 = 0.01437 -> ceil 0.02`.

At the deployed `<= 0.96` discipline, gross is **4.00%** of qty and the
taker fee **3.3013%**, so the net is **0.6987pp**. One cent of rounding on a
~$1 leg is **1.0pp** -- larger than the entire net margin. So viability is
**not monotone in size**; it is a comb:

| qty @ pair 0.96 | gross | fee | net | |
|---|---|---|---|---|
| 0.51 | 0.0204 | 0.02 | +0.0004 | PASS |
| 0.75 / 1.00 / 1.25 / 1.50 / 2.00 / 2.50 | | | <= 0 | **REFUSE** |
| 5.00 | 0.2000 | 0.18 | +0.0200 | PASS |
| 10.00 | 0.4000 | 0.34 | +0.0600 | PASS |

**The authorized clip range sits inside the refusing band.** And at pair
**0.97** -- which the shadow quoted in all three recorded windows, above its
own documented discipline -- gross 3.00% is below the 3.29% fee at EVERY
size: no quantity rescues it.

## 4. What this does to the $5/window objective

At the 0.96 discipline and the measured taker rate:

| target | pair cost needed per window |
|---|---|
| $0.50/window | $68.69 |
| $1/window | $137.39 |
| **$5/window** | **$686.94** |

- the authorized $4.65 cap yields a ceiling of **$0.0338/window**
  (~$3.25/day over 96 windows);
- deploying the **entire $344.41 account every window** yields
  **$2.51/window** -- still half the objective.

**So $5/window is arithmetically out of reach at the measured fee rate even
at 100% of the account per window**, before adverse selection, single-leg
fills or settlement risk. Capital is not the only binding constraint; the
fee rate is.

## 5. The one measurement that changes this, and it needs no live orders

**3.30 of the 4.00 points is fee.** So the effective rate dominates
everything, and it is currently UNIDENTIFIED. Among 28 single-fill legs
(taking qty=1, which is itself unverified): 9 match the taker formula
exactly, 2 match a maker rate, **4 carried zero fee**, and **2 carried a net
credit of -$0.0100** -- a rebate exceeding the fee.

| fee basis | net margin at pair 0.96 | $5/window needs |
|---|---|---|
| taker Theta = 0.0695 | 0.70pp | $686.94 |
| maker-style 0.0175 | 3.17pp | ~$151 |

Identifying it is worth **4.5x on the margin**. The evidence required is
already in the durable ledger and is not a trading experiment: **join
per-leg FILLED QUANTITY to the commission already recorded per leg.** The
inspect artifact carries cost and fees but not qty, which is the only
reason the rate cannot be read off today.

Until that is settled, the gate below prices the **worst case** (taker,
no rebate netted), which is the only safe direction for a refuse-only gate.

## 6. The change made

`src/l5_fee_quantum_gate.py` -- a refuse-only necessary-condition test:
a pair acquisition must prove its gross margin exceeds the rounded fee both
legs will actually pay. It arms nothing, clears no latch, raises no limit,
touches no state, and is not wired into the money path by this commit.

It refuses our own observed shadow quote (pair 0.97, qty 1.25) at
**net -$0.0225**, and refuses it at every size.

**It is a necessary condition, never a sufficient one.** Passing does not
make a pair profitable -- adverse movement, single-leg fills and settlement
risk are all outside it. Failing means the pair cannot pay its own rounded
fee and no price path saves it.

18 tests in `tests/test_l5_fee_quantum_gate.py`.
