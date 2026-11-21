# Flow ladder, first evidence — first fills at last, and they were toxic

**`GEMI-BTC05M2609201555-UP`, 2026-09-20T15:54:38Z. One window.**
The loose rungs produced the first fills this programme has ever had, and the
result is mostly negative. Recorded before it is acted on.

## The funnel

```
PP_F0            uniqObs=22 cand=22 rested=22 first=22 ffRate=1.00
                 compPrice=22  compFill=0  m2m=0.00
                 matched=0  residRate=1.00  mo60=-52.0  adv60=1.00
                 perDay first=10149  complete=0
PP_F1            uniqObs=10 cand=10 rested=10 first=10 ffRate=1.00
                 compPrice=10  compFill=0  m2m=0.00
                 matched=0  residRate=1.00  mo60=-52.0  adv60=1.00
PP_F2            0 candidates
PASSIVE_PAIR_V1  0 candidates   (= F5)
```

## 1. The complement price appeared every time and never filled

```
compPrice = 22        compFill = 0        maker_to_maker = 0.00
```

This is the exact failure the price/fill split was built to detect. An
acceptable complement price was available on **every single** first fill, and
the complement **never filled once**. The 0.34 best-pair-basis from profit
sprint #1 was price geometry, as flagged at the time. On this window it bought
nothing.

## 2. The fills were severely adversely selected, and all on one leg

```
mo8 = -22.0    mo30 = -36.0    mo60 = -52.0    adverse@60s = 1.00
byLeg = {'NO': n=22, completion_rate 0.00, markout@60s -52.0}
```

**All 22 first fills were on the NO leg. Not one YES fill.** The markout is
monotonically worse with horizon and adverse on 100% of observations.

That is the textbook adverse-selection signature: we were filled on NO precisely
because the market was running through NO bids, and we were the liquidity for a
one-directional move. A resting pair does not get "both legs filled" in that
regime — it gets the losing leg filled and the winning leg left behind.

## 3. No spread compression. The hypothesis it was meant to test is not
supported here

```
PP_F0 book move:  8s {MIXED 18, DIRECTIONAL 4}
                 30s {MIXED 22}
                 60s {MIXED 20, SPREAD_EXPANSION 2}
                120s {MIXED 14, DIRECTIONAL 2}
```

**Zero SPREAD_COMPRESSION at any horizon, in 22 candidates.** The two instances
that were not MIXED or DIRECTIONAL were EXPANSION — the book widening *away*
from where a passive pair would have rested.

So on this window, wide Gemini books did **not** compress after the moment we
would have posted into them. The "compression is the edge" hypothesis is
unsupported by this sample.

**A limitation of the classifier, stated rather than discovered later.** MIXED
dominates, and some of that is the classifier's granularity: `DIRECTIONAL`
requires the bid sum to move less than one tick, so a reprice with any sum drift
falls to MIXED. That makes MIXED over-inclusive. It does **not** affect the
headline: compression requires both bids up with the sum rising, which is
detected independently of how MIXED is drawn, and it happened zero times.

## 4. This supports the flow filter rather than undermining it

F0 and F1 are the rungs with **no meaningful flow evidence** — zero or one
print. They admitted, filled instantly, and were run over. That is exactly what
`FLOW_REVERSIBLE` and `NOT_CONCENTRATED` exist to prevent, and on this window
they would have prevented it.

**But the comparison the ladder exists to make is not yet computable.** F2 and
F5 had **zero candidates** in this window, so every `ECON_LADDER_DELTA` is
`None`. We cannot yet say whether ≥5 prints buys better fills — only that
≥0 prints buys bad ones.

## 5. A flag on the fill model itself

`ffRate = 1.00` — every rested rung filled, on both rungs. A 100% fill rate is
not plausible as a forward expectation and is a reminder that
`HypotheticalBook` is an explicit **upper bound**: size ahead is the displayed
size at placement and our own presence would have changed the flow being
replayed. On a wide book with a one-directional sweep, that upper bound is
probably close to its loosest.

It cuts both ways here: if fills are overstated, so is the adverse selection
attached to them. What is *not* model-dependent is that the complement never
filled while its price was continuously available — the same model was applied
to both legs.

## What this does and does not settle

- **Does not** kill the thesis: the rungs that failed are the ones with no flow
  evidence, which is not what the thesis proposes.
- **Does** kill the reading that a 0.34 passive basis is obtainable profit.
- **Does** give us, for the first time, real first-fill and completion evidence,
  which is what the sprint asked for.

Nothing was tuned. The next thing that matters is a window where F2/F5 admit,
so the incremental value of flow evidence becomes computable.
