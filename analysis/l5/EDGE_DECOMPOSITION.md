# DECOMPOSING THE CASH-CONFIRMED PRE-INCENTIVE L5 EDGE

Explains the baseline; does not optimize it. Full corrected corpus, charged
cashflow accounting, no 13-hour constants. Historical reconstruction of
another trader's public activity — **no figure is realized profit for our
account.** No threshold optimization, no OOS unseal, no live orders, clone
unchanged.

Artifacts: `scripts/l5_edge_decomposition.py`,
`analysis/l5/EDGE_DECOMPOSITION.json`.

Scope: **6,252 pre-incentive events, 336,342 fills, 100 active days,
measured cash +$19,596.32 = $195.96/day.** (A prior pass divided by a
hardcoded 110 days; the actual count of distinct days carrying a
pre-incentive event start is **100**, so the per-day figure is $195.96,
not $178.15. Corrected here.)

---

## ⚠ FIRST, A CONTAMINATION THAT QUALIFIES THE WHOLE RESULT

"Pre-incentive" was defined by the **first REWARD** record (2026-08-08).
But incentives began far earlier:

| first record | date |
|---|---|
| **MAKER_REBATE** | **2026-04-21** |
| TAKER_REBATE | 2026-06-20 |
| REWARD | 2026-08-08 |

So the window called "pre-incentive" contains **three and a half months of
maker rebates already flowing**. Measured on genuinely incentive-free
activity:

| window | events | days | cash | /day | median event |
|---|---|---|---|---|---|
| **before ANY rebate** (03-19→04-21) | **211** | **17** | **−$168.46** | **−$9.91** | **−$0.76** |
| before first TAKER_REBATE (→06-20) | 2,046 | 69 | +$9,758.44 | +$141.43 | +$1.58 |
| before first REWARD (→08-08, as used) | 6,252 | 100 | +$19,596.32 | +$195.96 | +$5.18 |

**The truly incentive-free period is slightly NEGATIVE** — though on only
211 events over 17 days, which is thin enough that it is better read as
*indistinguishable from zero* than as a demonstrated loss. Either way, the
claim "L5's trading stood on its own before incentives" is **weaker than I
reported last turn**: it rests on a period in which maker rebates were
already being credited. I am flagging this rather than restating the
earlier figure.

---

## Q1 / Q2 — Where the money comes from. Matched inventory IS the edge.

Attribution is a **split of the measured cash** derived from terminal
inventory, never a model added alongside redemption — so nothing is
double-counted, and the parts are asserted to reconcile.

| component | amount |
|---|---|
| **matched-pair economics** | **+$80,652.14** |
| directional residual settlement | **−$30,654.03** |
| charged transaction costs | **−$29,135.82** |
| sum of parts | +$20,862.28 |
| **measured cash** | **+$19,596.32** |
| unreconciled residual | **−$1,265.97** |

**Answer to Q1: yes, matched inventory is profitable, and it is the sole
source of profit.** The residual destroys **38%** of it and charged fees
destroy another **36%**. The edge is matched-pair compression, net of two
large, roughly equal leaks.

The −$1,265.97 unreconciled residual is the *same* figure the adversarial
audit found as redemption sitting below entitlement. It appearing
identically by two independent routes is reassuring as to arithmetic, but
its cause is still **not identified** — the merged-complete-set hypothesis
was tested and disproved (0 of 73 cases matched).

Terminal state across 6,252 events: **5,100 reached matched inventory**;
pair cost median **0.9679** (p25 0.9195, **p75 1.0136**, p90 1.0933) — so
over a quarter of events end with a pair costing **more than $1.00**;
matched % median 0.7034; residual % median 0.2967; residual won in 3,179
events.

Per-fill evolving state (UP qty / DOWN qty / matched / residual / each
leg's cost) is reconstructed for every event and stored in the JSON
`path` array, with each fill's relation to the previous one
(OPEN / EXTEND_LOWER / EXTEND_HIGHER / EXTEND_SAME / SWITCH) and its TTE.

---

## Q5 — Is $138–196/day a repeatable process or a handful of outliers?

**Both readings are partly right, and the headline statistic is
misleading. Here is the honest answer.**

| statistic | value |
|---|---|
| median event cash | **+$5.18** |
| win rate | **57.37%** |
| p10 / p25 / p75 / p90 | −$74.84 / −$23.12 / +$32.34 / +$69.45 |
| worst / best event | **−$528.77** / +$791.87 |
| max drawdown | **−$3,446.07** |
| longest losing streak | **11 events** |
| gross positive / gross negative | **+$151,929 / −$132,333** |

Concentration, by removal rather than by share:

| excluding | remaining cash | % of total | per remaining event |
|---|---|---|---|
| top 1 event | $18,804 | 96.0% | +$3.01 |
| top 5 | $16,578 | 84.6% | +$2.65 |
| top 10 | $14,638 | 74.7% | +$2.35 |
| **top 63 (1%)** | **$840** | **4.3%** | +$0.14 |
| top 100 | **−$6,211** | −31.7% | −$1.01 |

The "top 1% = 95.7% of profit" framing overstates the fragility: it is not
one or five lucky events, and the **trimmed mean after dropping the best
and worst 1% is still +$2.77/event**, with a positive median and a 57.4%
win rate. There is a genuine positive central tendency.

But the net is a **6.9% residue of $284,262 of gross flow** (+$151,929
against −$132,333). That is the real characterisation: **a high-churn,
thin-margin process whose net survives only because the loss tail is
slightly thinner than the win tail.** It is not an outlier artifact, and it
is not robust — removing the best 100 of 6,252 events turns it negative.

---

## Q6 — Time stability: profitable and unprofitable periods NOT averaged

| regime | events | days | cash | **/day** | /event | win % | median event |
|---|---|---|---|---|---|---|---|
| R1 (→04-24) | 386 | 21 | $885.63 | **$42.17** | $2.29 | 51.0% | $0.10 |
| R2 (04-25→06-22) | 1,684 | 49 | $9,031.59 | **$184.32** | $5.36 | 52.4% | $2.04 |
| R3 pre-incentive part (06-23→07-31) | 4,182 | 30 | $9,679.10 | **$322.64** | $2.31 | 60.0% | $6.61 |

Per-day **rises** monotonically across regimes while per-event **falls**
after R2 — the later period is more events at thinner margin, which is
"frequency before size" visible in cash.

Weekly: **20 weeks, 4 negative.** Worst week **−$2,415.60** (2026-W19);
best +$2,718.95 (W27). Early weeks W13/W14/W16 are all small negatives, so
the negative weeks are concentrated in the thin early period and one later
drawdown.

---

## Q7 — Capacity inputs (the backward-from-$480 work is in CAPACITY_AND_CLONE)

Capital actually deployed per event, measured as **peak USDC outlay**
during the event — the binding figure, not turnover:

| | median | p75 | p90 | max |
|---|---|---|---|---|
| peak outlay / event | **$316.05** | **$750.62** | **$1,131.30** | $3,050.06 |

Cash per deployed dollar: median **+2.22%**, p75 +12.02%, p90 +30.57%,
p10 −39.94%, **mean +0.61%**. The mean being far below the median is the
same fat-left-tail signature as Q5.

---

## What this changes

1. **The pre-incentive claim is weaker than reported.** The genuinely
   incentive-free window is 211 events / 17 days and is slightly negative.
   Maker rebates were already flowing from 2026-04-21 through most of what
   was called "pre-incentive".
2. **Matched-pair compression is nonetheless the real mechanism**:
   +$80,652 of matched economics, against a −$30,654 residual leak and
   −$29,136 of charged fees. If either leak were halved the result would
   roughly double.
3. **Fees are a first-order term, not a rounding error** — 36% of the
   matched gain. Any venue comparison must treat them as such.
4. **The process is thin-margin and high-churn**, not outlier-driven: net
   is 6.9% of gross. That is the characterisation to carry forward, and it
   is why execution quality matters more than entry selection here.
