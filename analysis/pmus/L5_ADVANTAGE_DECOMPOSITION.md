# L5 ADVANTAGE DECOMPOSITION

Read-only. No PMUS strategy change, no new rule, no parameter tuning. θ stays
0.96; the five-window sweep's θ=1.02 reading is NOT selected.

**PMUS reference** — frozen queue-aware θ=0.96 variant B, n=5 usable windows:

| | q=25 | q=50 |
|---|---|---|
| matched economics | +4.2825 | +7.9507 |
| directional residual | −4.0500 | −8.1000 |
| fees | −0.8007 | −1.5153 |
| **total/window** | **−0.5682** | **−1.6646** |

**L5 source** — `docs/L5_FULL_HISTORY_LEDGER_20260831.md`, complete Polymarket
history 2026-03-19 → 2026-08-31: 742,964 TRADE, 15,243 REDEEM, 109 MAKER_REBATE,
72 TAKER_REBATE, 19 REWARD rows across **16,501 up/down windows**, $7,423,850
turnover. Cash-based and exactly additive; reconciled per market against the
venue's own `cashPnl` at median abs diff $0.49.

## The waterfall

| component | total USD | $/window | ¢/$ turnover |
|---|---:|---:|---:|
| gross matched economics | +155,076 | +9.3980 | +2.0889 |
| directional residual & timing | −141,390 | −8.5686 | −1.9045 |
| **= net trading cash** | **+13,686** | **+0.8294** | **+0.1844** |
| fees | **0** | 0.0000 | 0.0000 |
| maker rebates (credited) | +33,929 | +2.0562 | +0.4570 |
| liquidity rewards (credited) | +26,956 | +1.6336 | +0.3631 |
| taker rebates (credited) | +7,323 | +0.4438 | +0.0986 |
| **= incentive income** | **+68,208** | **+4.1336** | **+0.9188** |
| **= realized trading economics** | **+81,895** | **+4.9630** | **+1.1031** |

**83.3% of L5's profit is incentive income.** His net trading cash on $7.42M of
turnover is +0.184¢/$ with a weekly-block bootstrap 95% CI of [−0.08, +0.55]¢/$
and P(≤0) = 8.4% — *statistically indistinguishable from zero*. Polymarket
charges **no trading fees**.

## Normalized comparison

| unit | L5 | PMUS q=25 | PMUS q=50 |
|---|---|---|---|
| fills/window | not separable from the fill feed | 9.0 | 9.0 |
| contracts/window | ~ (turnover $449.90) | 225.0 | 450.0 |
| two-sided fill rate | quotes both sides by construction | 5 of 5 (100%) | 5 of 5 (100%) |
| matched contracts/window | — (647,805 matched units in cohort) | 83.2 | 158.2 |
| **matched $/contract** | — (+2.0889¢/$ turnover) | **+0.051477** | **+0.050260** |
| **residual frequency** | **~100% (never sells)** | **20% (1 of 5)** | **20% (1 of 5)** |
| residual contracts/window | 8%→42% of volume by era | 10.0 | 20.0 |
| **residual $/contract** | **−0.0889** (weighted, all bands) | **−0.4050** | **−0.4050** |
| turnover/window | $449.90 | $148.86 | $294.25 |
| maker fraction | 30,414 maker / 6,510 taker = 0.824 | 0.7301 | 0.7399 |
| **fees/contract** | **0.000000** | **0.002598** | **0.002491** |
| **credited rebates/contract** | **+0.4570¢/$ turnover** | **0.000000** | **0.000000** |
| **rewards/contract** | **+0.3631¢/$ turnover** | **0.000000** | **0.000000** |
| net $/contract | — (+1.1031¢/$) | −0.001844 | −0.002737 |
| **net $/window** | **+4.9630** | **−0.5682** | **−1.6646** |

## 1. Why can L5 tolerate the same cheap-residual behaviour?

**He cannot — and he does not.** He is barely profitable on trading alone
(+$0.8294/window, CI spanning zero). Three measured reasons he survives it:

1. **A credited subsidy we do not have: +$4.1336/window.** It is 5.0× his entire
   net trading cash. Remove it and his business is noise around zero.
2. **Zero fees.** Polymarket charges none; we pay 0.0026/contract.
3. **His residual is diversified, ours is concentrated.** 36.2% of his residual
   units sit in *positive*-expectancy bands (55–75c, 75–90c, >90c). Ours was
   100% in the 25–45c band, his single worst at −0.2487/unit. His weighted
   residual is **−0.0889/unit**; ours is **−0.4050/unit** — 4.6× worse overall
   and 1.63× worse even within the same band.

## 2. Matched economics lost to residuals, before incentives

| | |
|---|---|
| **L5** | **91.17%** (141,390 / 155,076) |
| PMUS q=25 | 94.57% |
| PMUS q=50 | 101.88% |

**These are nearly identical.** The residual drag is *not* the differentiator —
it is a shared structural property of the strategy family.

## 3. Fraction of that residual loss recovered by rebates/rewards

| | |
|---|---|
| **L5** | **48.24%** (68,208 / 141,390) |
| **PMUS** | **0.00%** |

## 4. L5's observed incentive rate applied to PMUS volume — DIAGNOSTIC ONLY

L5's credited incentive rate is **0.9188¢/$ of turnover** (68,208 / 7,423,850).

| | turnover/window | hypothetical incentive | resulting net | verdict |
|---|---|---|---|---|
| q=25 | $148.86 | +$1.3677 | **+0.7995** | would turn POSITIVE |
| q=50 | $294.25 | +$2.7035 | **+1.0389** | would turn POSITIVE |
| q=25 at L5 peak 1.4¢/$ | | +$2.0840 | +1.5158 | POSITIVE |
| q=50 at L5 peak 1.4¢/$ | | +$4.1195 | +2.4549 | POSITIVE |

**Both clip sizes flip positive on the incentive term alone.** This is NOT
realized PMUS P&L and is not counted as such. The reward-program rate schedule
and eligibility rules are `UNTESTED_NOT_SOURCEABLE` for our account; 0.9188¢/$
is a Polymarket number earned under terms we have not verified apply to us.

## 5. Actual credited PMUS rebates applied

Credited rebate is **$0** — confirmed zero three independent ways, never
credited. Applying it changes nothing:

| | net/window |
|---|---|
| q=25 | −0.5682 (unchanged) |
| q=50 | −1.6646 (unchanged) |

## 6. Dollar attribution of the gap

Total per-window gap, L5 +4.9630 vs PMUS:

| component | q=25 | share | q=50 | share |
|---|---|---|---|---|
| gross trading (matched + residual) | +$0.5969 | 10.8% | +$0.9787 | 14.8% |
| fees (L5 pays $0) | +$0.8007 | 14.5% | +$1.5153 | 22.9% |
| **incentives (L5 credited, we get $0)** | **+$4.1336** | **74.7%** | **+$4.1336** | **62.4%** |
| **total gap** | **$5.5312** | | **$6.6276** | |

Not primarily fill rate, not primarily residual tail risk, not insufficient
turnover: **missing incentives, plus fees, are 89.2% of the gap at q=25 and
85.3% at q=50.** The genuine trading-economics gap is only $0.60–$0.98/window.

## 7. Tolerable 1830-like residual frequency

Using observed matched economics minus fees as the buffer:

| | buffer/window | 1830-like loss | break-even frequency | observed |
|---|---|---|---|---|
| q=25 | +$3.4818 | $20.25 | p ≤ 0.1719 = **1 in 5.8** | 1 in 5 (0.20) |
| q=50 | +$6.4354 | $40.50 | p ≤ 0.1589 = **1 in 6.3** | 1 in 5 (0.20) |

Observed frequency is **just above** tolerance at both clips — which is exactly
why the totals are mildly negative rather than badly so. The margin is thin: one
fewer such window in five would flip both signs.

## 8. Versus L5's actual terminal-residual behaviour

| band | units | share | $/unit |
|---|---|---|---|
| <10c | 13,128 | 5.8% | −0.0470 |
| 10–25c | 55,973 | 24.9% | −0.1584 |
| **25–45c** | **59,603** | **26.5%** | **−0.2487** |
| 45–55c | 14,706 | 6.5% | −0.2100 |
| 55–75c | 38,951 | 17.3% | **+0.0508** |
| 75–90c | 36,515 | 16.3% | **+0.1382** |
| >90c | 5,812 | 2.6% | **+0.0694** |
| **ALL** | **224,688** | 100% | **−0.0889** |

L5 **never sells** ($2.18 of sell proceeds across 742,964 trades), so every
unpaired contract is held to settlement: residual frequency ≈ **100%**, not 20%.

- **L5:** high frequency, low severity, diversified — a smooth −1.9045¢/$ drag,
  36.2% of units in positive-expectancy bands.
- **PMUS:** low frequency, high severity, concentrated — −2.7207¢/$ at q=25, all
  of it in his worst band.

So the comparison inverts the natural reading: **L5 does not tolerate rare large
residual losses, he runs a continuous small one and subsidises it.** Our problem
is not that we hold residual too often; it is that when we do, it is 4.6× worse
per unit and there is no subsidy behind it.

## Verdict

**TRANSFERABLE**
- Sequential (not simultaneous) pair formation; movement-driven complement
  choice. Independently confirmed on our tape: our completion edge correlates
  0.87 with the aged leg's unrealized gain, and no instantaneous pair arb exists
  (fresh two-sided cost 1.01–1.03 at every completion instant).
- Fast completion over patience: L5's pair cost is gap-invariant while residual
  time is pure cost.
- Horizontal scale at modest per-window size: matched $/contract is nearly
  flat across our clip grid (+0.0515 at q=25, +0.0503 at q=50), so size adds no
  edge per contract — more windows, not bigger clips.
- Matched-pair economics themselves: positive for L5 (+2.0889¢/$) and positive
  for us at every clip and every fill model.

**ADAPTABLE**
- Residual band composition. L5's residual is survivable largely because a third
  of it sits in positive-expectancy bands. Ours does not. Measuring and reporting
  residual-by-band on PMUS is diagnostic work, not a rule change, and is the
  highest-value adaptable item.
- Hold-to-settlement as the exit policy. It is L5's actual policy, but the ledger
  is explicit that whether it *beats* rescuing is an open counterfactual — it
  coexists with profit only because subsidies are large.

**NON-TRANSFERABLE**
- **The incentive stream.** +$4.1336/window, 83.3% of L5's profit, 74.7% of the
  gap. Credited rebate on PMUS is $0, and the program's rate schedule and
  eligibility are not sourceable for our account.
- **Zero trading fees.** A Polymarket property. Our ceil-per-fill taker fee
  changes pair economics structurally.
- **Tolerating >$1 pairs.** 30–45% of L5's matched pairs cost more than $1.00
  (median 0.975). That is a deliberate purchase of incentives with guaranteed
  trading losses. Without the incentive it is simply a loss, which is precisely
  why θ≤0.96 must stay locked.

## The single largest measured economic gap

**Credited incentive income: L5 +$4.1336/window (0.9188¢/$ of turnover, 83.3% of
his profit) against PMUS $0.00 — 74.7% of the entire per-window gap at q=25.**

L5's trading edge net of his own residual bleed is +0.184¢/$ with a confidence
interval spanning zero. Ours is −0.38¢/$ at q=25. The two businesses are close
to each other and both close to zero on trading alone. The difference between
+$4.96/window and −$0.57/window is almost entirely a subsidy we do not receive,
plus a fee he does not pay.
