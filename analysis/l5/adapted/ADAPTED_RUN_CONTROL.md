# ADAPTED — run control, chronological OOS

## `HISTORICAL_ACTION_FILTER_COUNTERFACTUAL` — not executable replay

A historical L5 fill surviving a run-cap filter is **not** evidence our maker
order would have filled there. We held no queue position, and removing his
fills does not replace the flow that produced them. **This can justify a
prospective shadow candidate and nothing more.** It is not a backtest.

Separate lineage from FIDELITY. Nothing here is evidence about what L5 does or
why. **Incentives are excluded from all qualification economics.**

## Protocol, fixed before the answer was seen

Preregistered family: L5 control + run caps 2, 3, 5, 8, 13, 21. Selection on
TRAIN only — highest total P&L among rules retaining ≥80% of L5's TRAIN matched
quantity — then applied **once** to untouched OOS. Every cap's OOS result is
reported so a winner cannot hide instability.

**The 70/30 count split is degenerate here**: 313 of 527 windows are in August,
so it leaves an OOS spanning **two days** (08-30 → 08-31). Enough to score a
rule, useless for regime stability. A second **regime split** — spring
(Mar+Apr, 214 windows) → August (313 windows) — is reported alongside it.

## Regime split: spring → August

| rule | SPRING P&L | AUGUST P&L | matched% (Aug) | pair (Aug) | maxDD (Aug) |
|---|---|---|---|---|---|
| **L5_CONTROL** | **+429.53** | **−580.40** | 100% | 0.9610 | −2,754 |
| cap_2 | +246.54 | **+2,741.98** | 56% | 1.0011 | −817 |
| cap_3 | +316.23 | +2,323.14 | 69% | 0.9955 | −912 |
| cap_5 | +331.40 | +1,665.43 | 83% | 0.9866 | −887 |
| **cap_8** *(chosen on spring)* | **+479.85** | +1,375.14 | 92% | 0.9771 | −1,189 |
| cap_13 | +458.25 | +575.84 | 98% | 0.9692 | −2,178 |
| cap_21 | +433.33 | −83.17 | 100% | 0.9637 | −2,684 |

**Two findings, and the second is a correction to what I reported earlier.**

**1. The cap ordering inverts between regimes.** In spring the *loosest* caps
win (cap_8 > cap_13 > cap_21 > control); in August the *tightest* do
(cap_2 > cap_3 > cap_5 > cap_8 > cap_13 > cap_21 > control). A cap chosen in
one regime is not the right cap in the other. Only **caps 5, 8 and 13 are
positive in both.**

**2. L5's trading P&L is not "flat" — it flips sign.** Spring **+429.53**
(ret/\$ **+0.0153**, genuinely good); August **−580.40** (ret/\$ −0.00115). The
aggregate −\$151 I reported is two regimes cancelling, not a steady state.

## 70/30 split, for completeness

Selection chose **cap_5** (TRAIN +924.80 among rules ≥80% matched). OOS:
**+1,072.04**, 82.6% matched retained, residual P&L −1,312 against control's
−8,168, max drawdown −650 against −824. Positive and above control's +977.05 —
but **cap_8 (+1,418) and cap_13 (+1,416) both did better OOS**, so the TRAIN
selection did not find the OOS optimum. That is exactly what reporting every
cap is for.

## Against the advancement gate

The gate asks for a rule positive on untouched OOS, with meaningful matched
capacity and materially reduced residual loss. On both splits the selected rule
clears it:

- 70/30 → cap_5: **+1,072 OOS**, 83% matched, residual loss cut 84%.
- regime → cap_8: **+1,375 OOS August**, 92% matched, residual loss cut 57%.

**But the cap value is not robustly identified.** The two protocols select
differently, and the ordering inverts across regimes. The defensible statement
is *a cap in the 5–13 band*, not a specific number; every member of that band
is positive in both regimes.

I have **not** defined the prospective candidate. The gate is met on the
letter, but choosing between cap_5 and cap_8 on this evidence would be picking
a winner the data does not identify — and that is the failure this protocol
exists to prevent. That choice is yours.
