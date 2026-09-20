# Profit sprint #1 — which blocked states would have been good fills

**Window `GEMI-BTC05M2609201510-UP`, 2026-09-20T15:09:34Z. n=34 eligible cycles,
one contract, one direction of BTC move.** Every number below is small-sample
and from a single window. Nothing here has been deployed and no threshold has
been moved.

---

## 1. Gate attribution — SPREAD is binding, the flow gates are passengers

```
ECON_GATE cycles=34 admitted=0
  FLOW_REVERSIBLE   PASSENGER  blk=71%   marg=0%
  NOT_CONCENTRATED  PASSENGER  blk=18%   marg=0%
  SPREAD            BINDING    blk=100%  marg=29%
```

`SPREAD` fails on **every** cycle and is the **sole** blocker on 29% of them.
The flow gates block 71% and 18% respectively and are marginal on **none** —
removing either alone admits nothing, because `SPREAD` was failing on those
cycles too.

```
ECON_ABLATE current=0 ; no_spread_gate=10(+10) ; flow_only=10(+10) ;
            quote_quality_only=0(+0) ; flow_plus_economics=10(+10) ;
            economics_only=34(+34)

ECON_COMBO  FLOW_REVERSIBLE+SPREAD  n=18 (53%)
            SPREAD                  n=10 (29%)
            FLOW+NOT_CONCENTRATED+SPREAD n=6 (18%)
```

`economics_only = 34` is worth stating plainly: `ECONOMIC_FEASIBILITY`,
`COMPLEMENT_REACHABLE`, `TWO_SIDED_QUOTE` and `INVENTORY_HEADROOM` pass on
**every** cycle. The economics gates are not what is stopping us.

## 2. Was being blocked good for us? Split by what blocked it

```
BY MARGINAL BLOCKER
  SPREAD only    n=10  mo8=-0.5  mo30=+22.0  mo60=+11.5  adv60=0.5  maxAdv=-6.0
  MULTI_BLOCKED  n=24  mo8=+1.0  mo30= -5.0  mo60= -3.0   adv60=0.5  maxAdv=-32.0
```

**This is the decisive comparison of the sprint.** The states blocked *only* by
the spread bound — the ones that passed every flow and quality gate — had
**positive** markout at 30s (+22 ticks) and 60s (+11.5), with a shallow worst
drawdown of −6 ticks. The states where a flow gate *also* fired had **negative**
markouts and a −32 tick worst drawdown, five times deeper.

So on this sample the flow gates are doing their job and **the spread gate is
blocking the good states.**

## 3. Why wide spreads are where the pair economics live

```
BLOCKED overall  n=34  opposite_available=1.00  time_to_opposite=9.85s
                       best_pair_basis=0.34     best_pair<=1.00 = 1.00
```

**Every** blocked candidate had an economically acceptable complement appear,
within a median of **9.85 seconds**, at a best pair basis of **0.34**.

That is not an accident of this window, it is the venue's arithmetic.
`metadata.outcome_book` already records it: *"the passive pair cost is sell.yes
+ sell.no — measured 0.19 + 0.50 = 0.69 on GEMI-BTC05M2609181115-UP, against
1.31 to cross both asks."* A resting BUY joins each outcome's **bid**, so the
passive pair costs `yes_bid + no_bid` — and **a wide spread is exactly what
pushes that sum below $1.00.**

`max_spread = 0.08` therefore screens out precisely the states where passive
pair economics are best. It is not protecting us from bad inventory; on this
sample it is **making participation impossible in the states worth
participating in.**

**The necessary caveat:** `opposite_available` and `best_pair_basis` are
**price availability, not fills**. A basis of 0.34 is what the two bids summed
to, not a pair we would have been hit on. Whether both legs actually fill is the
hypothetical book's question, and first fills remain rare.

## 4. YES and NO are not symmetric — and my earlier reading was wrong

```
BTC5m|121-300|NO   n=12  mo8= 0.0  mo30= +1.5  mo60= -1.5  adv60=0.5  maxAdv=-11.0
BTC5m|121-300|YES  n=12  mo8=+3.0  mo30=-17.0  mo60= -3.0  adv60=0.5  maxAdv=-63.5
BTC5m|61-120|NO    n=5   mo8=+4.0  mo30=+41.5  mo60=+65.0  adv60=0.0  maxAdv= -4.0
BTC5m|61-120|YES   n=5   mo8=-18.0 mo30=-12.5  mo60=-42.0  adv60=1.0  maxAdv=-18.0
```

In the 61–120s region the two legs are near mirror images: **NO is +65 ticks at
60s with 0% adverse; YES is −42 ticks with 100% adverse.**

**This corrects my earlier report.** I flagged the census cell
`BTC5m|61-120|0.56-0.75 signed=-44, adverse=1.00` as "late-window BTC5m is
adverse". That census figure is computed from the **YES-buyer perspective only**
(`MARKOUT_PERSPECTIVE = MAKER_BUY_YES_AT_BID`). Segmented by leg, the same
region is strongly **favourable** to a NO maker. The signature — roughly equal
and opposite — is a **directional move in BTC during the sample**, not an
intrinsic property of late-window contracts. Reporting it as structural toxicity
would have been wrong, and the leg segmentation is what caught it.

n=5 per cell. This is a hypothesis to re-test, not a finding.

## 5. The answer to the sprint's question

> Which currently blocked states would have been good first fills, and what
> pre-fill information distinguishes them from the bad ones?

**The good ones are the states blocked by SPREAD alone.** They are distinguished
from the bad ones by **information we already compute before the fill**: they
pass `FLOW_REVERSIBLE` and `NOT_CONCENTRATED`. On this sample that split
separates +22/+11.5 tick markouts with −6 drawdown from −5/−3 tick markouts with
−32 drawdown.

No new pre-fill feature is needed to make the distinction. The flow gates
already make it; the spread gate is overriding them.

## 6. Proposed smallest frozen entry rule — PROPOSED, NOT DEPLOYED

Preregistered here and **not applied**, per the instruction not to deploy
threshold changes from the same observations that suggested them.

**Replace the boolean `spread <= max_spread` with an economic test of the
passive pair, keeping every other gate byte-for-byte:**

```
PASSIVE_PAIR_ECONOMIC   (replaces SPREAD)
    yes_bid + no_bid + fee(yes_bid) + fee(no_bid)  <=  1.00 - min_edge
```

Properties, stated before any data is collected under it:

- it uses spread as an **economic input, not a boolean** — a wide spread helps
  this test and a tight one hurts it, which is the correct direction and the
  opposite of the current gate;
- it is **one line and one threshold** (`min_edge`, already preregistered at
  0.005), so it adds no new tunable;
- it cannot admit a state whose passive pair is unprofitable, which is the
  protection `max_spread` was standing in for;
- a 25¢ spread passes when the two bids sum low enough; a 2¢ spread fails when
  they do not — the brief's own framing.

**It does not address adverse selection.** That is the flow gates' job, and this
sample says they are already doing it. If a later sample shows the flow gates
failing to screen toxicity, the answer is a better toxicity test, not a
reinstated spread bound.

### Before it may be deployed

1. ≥ 3 cohort-days, not one window, and BTC moving in both directions —
   the 61–120s asymmetry is currently confounded with one move;
2. `SPREAD`-only-blocked n ≥ 100 (currently 10), with the markout advantage
   over `MULTI_BLOCKED` still present;
3. the same split holding **separately for YES and NO**;
4. the counterfactual fork ledger showing the risk overlay is not itself the
   binding problem.

Until all four hold, `max_spread` stays at 0.08 and this rule stays on paper.
