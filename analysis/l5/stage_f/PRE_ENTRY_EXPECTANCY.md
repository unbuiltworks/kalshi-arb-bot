# Can positive two-sided expectancy be identified BEFORE submission?

## **VERDICT: NO-GO**

Read-only. Existing recorded data, the existing 384-window shadow control, and
venue-confirmed campaign results. No new infrastructure, no live trading, the
executor and watchdog untouched.

---

## 1. The question has an arithmetic answer

```
E[net] = P(both legs fill) * edge_per_contract * qty
       - P(one leg fills)  * adverse_residual_cost_per_unit
```

| term | value | source |
|---|---|---|
| edge per matched contract | **+0.017315** | live, qty-weighted effective pair 0.982685 over 15.11 venue-confirmed matched contracts |
| residual loss per unit | **−0.2315** | shadow, `residual_net −210.1843` over 908 residual units, 384 controlled windows |
| **break-even joint-fill share** | **93.0%** | `adv / (adv + edge)` |

**A pre-entry rule must lift the joint-fill share above 93% to break even.**

Observed, nowhere close:

| | |
|---|---|
| live windows ending with a residual | **7 of 7 (100%)** |
| live windows matching anything at all | 4 of 7 (57%) |
| shadow treatment windows matching nothing | **212 of 384** |
| shadow treatment windows with residual ≥3 | **254 of 384** |

## 2. The residual is adversely selected, not a fair coin

This is the finding that decides it, and it corrects my own earlier framing
that the residual was "a coin flip."

On our 7 live windows: 3 strands won **+$3.4042**, 4 lost **−$3.7413**, net
**−$0.3371** — at n=7 that is indistinguishable from a fair coin, and I should
not have characterised it either way from that sample.

On the only sample large enough to tell, 384 controlled windows:

| | per unit |
|---|---|
| matched pairs | **+0.2703** |
| **residual** | **−0.2315** |

**A fair coin on a liquid binary would sit near zero before fees. −0.2315 per
unit is systematic.** The mechanism is `adverse_rate 0.89`: the leg that fills
*alone* is the leg the market was already moving toward, so the strand is the
side about to lose. **Being filled is itself the bad news.**

## 3. There is no pre-entry observable with the needed variance

| candidate | recorded distribution | verdict |
|---|---|---|
| **spread** | 1 tick at **p10, p50 and p90** | **no variance.** `pair = 1 − spread − 2·d·t` makes the planned pair a constant **0.95** at depth 2. Nothing to sort on. |
| touch depth | p10 152/70 · p50 757/685 · p90 1842/1518 | real variance, **wrong sign**: more depth is more queue *ahead* of us. `queue_ahead_multiple_of_need` ran **73×–537×** — we are always far back. |
| price level | already gated to own 0.20–0.60 | the band is in force; it did not prevent 7/7 strands |
| time to settlement | `entry_cutoff 600s`, `complete_cutoff 720s` | bounded already; shortening it cuts fill chances with the risk |

**The planned pair is 0.95 by identity; the realized effective pair was
0.9827.** That 0.033 shortfall is the adverse cost measured a second way — and
it arises from the market moving **after** we commit. That is unobservable
before submission *by construction*, not for want of a feature.

## 4. Quote-placement rules evaluated

| rule | economic motivation | result |
|---|---|---|
| **require edge > adverse cost** | only enter when `(1 − pair) >` per-contract adverse cost | **unsatisfiable as a filter**: planned edge is a constant 0.05 and per-contract adverse cost ≈0.046. It admits every state or none; it cannot discriminate. |
| **quote at the touch** | higher fill probability | the shadow's own at-touch figures in those states were pairs of **1.08–1.28** — guaranteed losses. More fills, worse pairs. |
| **deeper behind the touch** | cheaper pair | fills less, and the complement drifts further; this is the current depth-2 setting and it produced 7/7 strands |
| **thinner books only** | less queue ahead | thin books are where adverse selection is worst; inverts the problem rather than solving it |
| **two-sided vs one-sided** | more matched volume | already measured: **+211 matched units, net −$41.50 (−$0.1081/window)** |

## 5. Sensitivity to execution assumptions: the conclusion is invariant

Shadow incremental net across the three fill regimes:

| scenario | incremental net |
|---|---|
| CONSERVATIVE | **−41.50** |
| BASE | **−37.65** |
| OPTIMISTIC | **−37.65** |

**Two-sided loses under optimistic fills.** A rule whose case rests on better
fill assumptions has nowhere to go.

One correction to a figure I quoted earlier: the shadow's `fill_rate 0.8053`
is `filled / (filled + unfilled_at_expiry)` = 2849/3538, not
`filled / quotes_placed` = 2849/3675 = 0.7752. I computed the latter and
should have matched the reported denominator.

## 6. Per-window economics, venue-confirmed

| window | matched P&L | residual P&L | net | matched vol | residual |
|---|---|---|---|---|---|
| `1330z` | +0.056403 | −0.898403 | −0.8420 | 4.33 | 2.14 UP |
| `1345z` | +0.110448 | +1.016352 | +1.1268 | 1.98 | 2.05 UP |
| `1400z` | −0.010321 | +1.223021 | +1.2127 | 2.14 | 2.09 UP |
| `1415z` | 0.000000 | +1.164800 | +1.1648 | 0 | 2.08 UP |
| `1430z` | 0.000000 | −0.949400 | −0.9494 | 0 | 2.02 DOWN |
| `1445z` | 0.000000 | −0.969600 | −0.9696 | 0 | 2.02 UP |
| `1500z` | +0.105080 | −0.923880 | −0.8188 | 6.66 | 2.00 DOWN |
| **total** | **+0.261610** | **−0.337110** | **−0.075500** | **15.11** | **7/7** |

**Net per window −$0.0108.** Maximum single-window loss **−$0.9696**.
Residual frequency **100%**.

## 7. Why no rule is recommended, and the fit I refused to make

7 windows, 4 of them informative, against ≥3 free parameters in even a
two-feature threshold rule. **Any rule fitted here is fitted to noise**, and
selecting a window subset by its own realized P&L is precisely the leakage the
brief forbids. I did not fit one.

**And reducing residual frequency would not change the verdict** — the brief
says so and the arithmetic agrees: matched is only +0.0173/contract, so
eliminating *every* strand would have earned **+$0.2616** across seven
windows, about **4 cents a window**, before the matched volume that strand-
avoidance would itself forgo.

## 8. NO-GO

The pre-entry question is answerable and the answer is no: the planned edge is
a constant, the shortfall is created after commitment, the break-even
joint-fill share is 93% against an observed 0%-of-windows-without-residual,
and the residual is adversely selected at −0.2315/unit rather than neutral.

**What would change this** — stated so it is falsifiable rather than as
encouragement: a venue or series where the spread has real variance (so the
pair edge can be sorted on), or a maker position closer to the front of the
queue (`queue_ahead_multiple_of_need` nearer 1 than 73), or a confirmed maker
rebate large enough to cover 0.046/contract. Rebates remain
**`UNVERIFIED_NO_VENUE_CREDIT_RECORD`, $0.00 credited** across every campaign
to date, so that last one is not available today.

Existing executor, watchdog and risk controls intact. No infrastructure added.
No live authorization requested.
