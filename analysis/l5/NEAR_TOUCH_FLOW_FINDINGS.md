# Near-Touch Flow: what the existing shadow can and cannot settle

Run against `analysis/l5/NEAR_TOUCH_FLOW_PREREGISTRATION.md` (committed
`f2b63da2`, before this cut). Read-only, from an already-running service's
logs. **No deployment.**

**Headline: the near-touch effect survives holding flow constant — the single
strongest piece of evidence we have. But near-touch posting happens almost
exclusively in busy, moving markets, so the two cannot be fully separated. And
the owner's advancement gate remains unevaluable, for the reason recorded
before the cut.**

---

## 1. The corrected baseline

| horizon | all proposals | 2-tick band |
|---|---:|---:|
| 300 s (frozen verdict) | 3.24% | 47.6% |
| 1800 s (research) | **25.23%** | **76.2%** |

Implied one-sided residual incidence at 30 minutes: **23.8% near-touch against
74.8% overall**. A 3× edge, not the 23× my earlier horizon-mismatched
comparison implied.

## 2. Distance is not merely a proxy for flow — within high flow it still decides

Near = ≤2 ticks, far = ≥4 ticks, 506 measured proposals:

| stratum | near n | near fill | far n | far fill | contrast | usable |
|---|---:|---:|---:|---:|---:|---|
| **flow tercile: high** | 21 | **42.9%** | 130 | 3.9% | **0.390** | yes |
| flow tercile: mid | 1 | — | 140 | 1.4% | — | no |
| flow tercile: low | 3 | — | 134 | 0.0% | — | no |
| movement tercile: high | 25 | 40.0% | 109 | 6.4% | 0.336 | yes |
| movement tercile: low | 0 | — | 226 | **0.0%** | — | no |
| sport: nfl | 25 | 40.0% | 400 | 1.8% | 0.383 | yes |
| market type: spread | 21 | **47.6%** | 97 | 4.1% | **0.435** | yes |
| market type: moneyline | 2 | — | 301 | 0.0% | — | no |

**Holding flow in its top tercile, near-touch still fills 42.9% against 3.9%.**
That is the falsifier-2 test and it does not fire: distance carries real
information beyond flow.

## 3. But the cohort lives in one corner, and that is the honest limit

Look at the `near n` column. Of 25 near-touch proposals:

```
flow tercile      high 21    mid  1    low  3
movement tercile  high 25    mid  0    low  0
spread tercile    high 25    low  0
```

**Essentially every near-touch proposal we have was made in a busy, moving
market.** So we cannot separate *"posting near the touch works"* from *"we only
post near the touch when the market is already alive"* — there is no usable
comparison cell outside that corner, and there never will be from this data,
because the selector does not put us there.

What that means for an instruction: **"post within two ticks" and "select
markets with flow and movement" cannot be prescribed independently on this
evidence.** They must be adopted together or not at all.

## 4. The number that should worry us most

```
movement tercile: low     far n = 226     fills = 0     fill rate 0.0%
market type: moneyline    far n = 301     fills = 0     fill rate 0.0%
```

**In quiet markets, nothing fills. Not rarely — zero, across 226 proposals.**
That is the mechanism the whole programme has been missing stated as plainly
as the data can state it: a passive complement is not a price, it is a bet
that someone will come and trade against it, and in a still book nobody does.

It also explains the R5F cohort's failure without reference to any policy
error: the live engine was posting complements into exactly this regime.

## 5. The gate is still unevaluable, exactly as pre-registered

> *"Does the ≤2-tick/high-flow cohort produce positive expected realized
> economics after assigning actual settlement outcomes to every residual?"*

**Cannot be answered.** Scoring a residual needs its size, side, cost basis and
the market's resolution joined on one row; the shadow emits aggregates, and the
logs carry residual size per market and none of the rest.

And even with the emission, the arithmetic is not yet there: the 2-tick band is
**n = 21**, essentially all NFL spread markets, which at 76.2% completion is
**about five residuals**. Five is not a loss distribution. The cohort has to
grow before the gate means anything, whatever we emit.

**No modelled residual loss is offered in its place.** The owner's warning —
that one residual can erase several successful pairs — is precisely why a
placeholder estimate here would be worse than no answer.

## 6. What would change that, and what it costs

One read-only emission change to a service that already has no order path: a
row per resolved proposal carrying distance, flow, band, spread, price,
residual size and side, status, time-to-fill. Then the gate becomes computable
as the cohort accumulates, at zero market risk.

That is a deployment. It is the owner's call, and it is the same call in
reverse from the wide-spread test he declined — that one would have tested a
hypothesis this evidence argues against; this one would make the surviving
hypothesis measurable.

## 7. Standing conclusions, unchanged

R5F stays off. The stopped probe is untouched. Today's **−$9.3736
venue-confirmed realized / +$0.2500 credited** is the failed baseline. The
cricket / `falf-ter` / `kaycro` completeness gap remains separate and open.

---

*Read-only. No deployment, no orders, no live restart.*
