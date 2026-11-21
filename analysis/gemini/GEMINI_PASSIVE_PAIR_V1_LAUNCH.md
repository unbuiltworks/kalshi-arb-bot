# PASSIVE_PAIR_V1 — launched, and what the first windows show

**Launched 2026-09-20T15:34Z** beside the immutable control and the unchanged
0.08-spread arm. Same tape, same 8-second cadence, same hypothetical fill model,
no mutations.

## The rule did exactly what it was designed to do

```
ECON_ABLATE PASSIVE_PAIR_V1 | current=0 ; no_spread_gate=0(+0) ;
            quote_quality_only=24(+24) ; economics_only=24(+24)
ECON_COMBO  PASSIVE_PAIR_V1 | FLOW_REVERSIBLE+NOT_CONCENTRATED  n=24 (100%)
```

**`PASSIVE_PAIR_ECONOMIC` passed on all 24 cycles** — it does not appear in a
single blocking combination. The spread bound is gone from this arm's refusals
entirely, which is the change it was built to make. Compare the boolean arm on
the same tape, where `SPREAD` blocked 100%.

## But the arm still admits nothing, and the blocker has moved

100% of refusals are now `FLOW_REVERSIBLE + NOT_CONCENTRATED`, and the reason is
not subtle:

```
ECON_WHY PASSIVE_PAIR_V1
  FLOW_REVERSIBLE   'only 1 prints in the window, below the minimum of 5'
  NOT_CONCENTRATED  'a single print is 100.0% of window volume, above 45.0%'
```

**One print in the whole flow window.** That is a genuinely illiquid contract,
not a mis-set gate — a tape with one print cannot evidence reversible two-sided
flow under any threshold, and `quote_quality_only=+24` says removing those gates
would admit all 24 states on the strength of no flow evidence whatsoever.

`first_fills = 0`, `rungs_rested = 0`, and **no `ECON_FUNNEL` line exists yet**,
because the funnel only opens on an admitted entry. The fill-sequence question —
the one the arm was built to answer — is still unanswered.

## The blocked states in this window looked good, and both legs at once

```
BLOCKED n=24  mo8=0.0 mo30=+9.0 mo60=+18.5 mo120=+31.5
              adverse60=0.21  maxAdverse=0.0  basis=0.785  b<=1=1.00

BTC5m|121-300|NO   n=12  mo60=+46.0  adverse60=0.42
BTC5m|121-300|YES  n=12  mo60=+17.5  adverse60=0.00
```

Note that **both legs are favourable simultaneously**, which cannot happen from
a directional move — a rising YES bid and a rising NO bid together means **the
spread compressed**. That is precisely the outcome a passive maker resting on
both bids wants, and it is the mechanism the thesis rests on.

It is also one window, n=12 per leg, with `maxAdverse = 0.0` across every
candidate — a suspiciously clean number that most likely reflects a single
benign window rather than a property of the venue. Treat it as encouraging
rather than as evidence.

## What is now blocking the experiment

The dependency chain is unchanged in shape but has moved one link along:

```
flow gates block  ->  no entries  ->  no first fills  ->  no fill sequence
                  ->  the thesis stays untested
```

The spread constraint is resolved. **Illiquid windows are now the binding
constraint**, and that is a different kind of problem: it is not a threshold to
loosen but a question of how often BTC5m windows carry enough two-sided flow to
evidence anything. That frequency is itself part of the capacity answer the
brief asks for — if only a small fraction of windows have real flow, the ceiling
on this strategy is set by that fraction regardless of how good the fills are.

**Nothing has been tuned.** `min_prints=5` and `min_alternation=0.35` are the
preregistered values and stay there; loosening them now, in the window that
exposed them, would be fitting a threshold to one thin tape. The next question
is empirical and needs more windows: what share of BTC5m windows carry ≥5 prints
with two-sided flow, and do those windows admit and fill?
