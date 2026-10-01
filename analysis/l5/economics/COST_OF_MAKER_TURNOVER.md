# Cost of maker turnover vs the PMUS maker credit. Answer: essentially no.

The objective is restated: **maker turnover is the product, trading loss is its
acquisition cost.** The hurdle is the PMUS credit on that turnover.

## The hurdle curve

`rebate = 0.0125 × C × p × (1−p)`, and cost deployed is `C × p`, so

> **credit rate = 0.0125 × (1 − p)** of cost — **max tolerable trading loss**.

| p | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | **0.50** | 0.60 | 0.70 | 0.80 | 0.90 | 0.95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| max loss % of cost | 1.188 | 1.125 | 1.000 | 0.875 | 0.750 | **0.625** | 0.500 | 0.375 | 0.250 | 0.125 | 0.063 |

**The credit per dollar deployed is highest on CHEAP sides**, not at the midpoint —
1.19% at 5¢ against 0.06% at 95¢. The absolute ceiling at any price is **1.25%**.

## LIVE PMUS — venue-credited numbers only

| cohort | mkts | turnover | pair | residual | credited | **trading/turnover** | credit/turnover | net/turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL** | 26 | $56.06 | +5.5644 | −7.4725 | 0.4200 | **−3.4038%** | 0.7492% | −2.6546% |
| R1 | 11 | $0.44 | +0.0316 | −0.0622 | 0 | **−6.9451%** | 0 | −6.9451% |
| R2C | 6 | $0.21 | +0.0132 | −0.0350 | 0 | **−10.2927%** | 0 | −10.2927% |
| R2C2 | 3 | $0.16 | +0.0082 | −0.0662 | 0 | **−36.7089%** | 0 | −36.7089% |
| R3L | 3 | $34.64 | +4.4753 | −6.2855 | 0.2800 | **−5.2257%** | 0.8083% | −4.4174% |
| **R4** | **3** | **$20.61** | +1.0361 | −1.0236 | 0.1400 | **+0.0607%** | 0.6794% | **+0.7400%** |

Trading = pair + residual − fees, i.e. **excluding** the rebate.

**Overall and in four of five cohorts the cost of maker turnover is 3.4%–36.7% of
turnover — 2.7× to 29× the 1.25% ceiling. Impossible at any price band.**

## Do we have ANY subset below its applicable rebate rate? One, and it is not usable

**R4 only.** Trading **+0.0607%** of turnover (a small gain, not a loss), credited
rebate 0.6794%, net **+0.74%**. Any positive credit clears a negative cost, so on
its face R4 passes.

It cannot be relied on:

* **3 markets**, and `markets_with_venue_realized: 2` — only two carry venue
  numbers.
* **$20.61 of turnover**, **$0.14 of credited rebate**, **$0.1525 of net**.
* The repository's own gates already record **G1 and G2 as NOT met**
  (`H_gates.all_met: false`). G1 is precisely "realized net ≥ 0 over ≥ 5 at-size
  markets AND the per-market mean exceeds its standard error."
* The at-size population it belongs to has **mean −$0.2296 per market against a
  standard error of $1.4181** — the sign is not established.
* **A chronological OOS test is not possible at n = 3.** I did not attempt one;
  reporting a split of three markets would be theatre.

**So: no subset survives. One candidate exists and fails on sample size.**

## Capacity, at R4's own rate

| | |
|---|---:|
| turnover per market | $6.869 |
| net per market at 0.74% | **$0.0508** |
| markets/day for $300/day | **5,902** (turnover $40,541/day) |
| markets/day for $480/day | **9,443** (turnover $64,865/day) |

Even granting the rate, the required market count is **two to three orders of
magnitude** beyond anything observed. Capacity, not rate, is the binding
constraint.

## REPLAY, reported separately and NOT mixed

Fresh-OOS V1 q10, 35 untouched windows, conservative Case-1: trading
**+0.9285%** of turnover, modeled rebate 0.3347% (**modeled, not credited**).

**Replay says the trading cost is negative; live says −3.4038%.** That is a
**4.3-percentage-point contradiction** on the only quantity that matters. The live
figure is venue-credited and the replay figure rests on a fill model whose last
three defects I had to repair. **Where they disagree, live governs.** Replay must
not be used to argue the hurdle is cleared.

## SHADOW

`cross_shadow_live` rows carry `shadow_only` and no credited rebates. There is no
executed maker notional and no venue credit, so a cost of maker turnover is **not
computable** from shadow. Reported as absent rather than estimated.

## The lever investigation, and why I did not run it

The assignment lists price band, time remaining, spread, volatility, book
imbalance, distance from touch, one- vs two-sided, inventory, residual cap and
requote behaviour as candidate reducers of the cost.

**The live evidence cannot support that analysis.** Conditioning a 26-market,
$56-turnover sample — whose largest cohort is 3 markets — on ten state variables
would produce subsets of one or two markets. The repo already shows what that
yields: R2C2's −36.7% is three markets. I am not going to search a 26-market
sample across ten dimensions until something clears 0.625%; that is the
definition of finding a subset by searching, which the assignment forbids.

What the evidence does support is one structural observation: **the credit rate
rises as price falls**, so cheap-side quoting has up to **19× the tolerance of
deep-favourite quoting** (1.19% at 5¢ vs 0.06% at 95¢). That is a property of the
schedule, not a finding about our execution, and it is the one lever visible
without data mining.

## Decision gate

> PMUS survives only if maker turnover's all-in trading cost is reliably below the
> maker compensation earned on it.

**Not demonstrated.** Live cost is 3.4% of turnover against a 1.25% ceiling. The
single passing cohort is 3 markets, 2 with venue numbers, $0.14 of credited rebate,
and fails the repo's own pre-existing gates.

What would change the answer is **not** more analysis of this sample. It is
executed maker fills with credited rebates at a volume where the sign of the
trading cost can be established — the repo's own G1 threshold of ≥5 at-size markets
with the mean exceeding its standard error, which has never been met.

## Classification

**OBSERVED** — every live figure above is venue-credited; the gate statuses; the
credit-curve algebra; the replay totals.

**INFERRED** — the capacity arithmetic, which assumes R4's rate holds.

**UNKNOWN** — whether the R4 rate is real or a 3-market artifact; whether any state
conditioning reduces the cost, because the sample cannot answer it.
