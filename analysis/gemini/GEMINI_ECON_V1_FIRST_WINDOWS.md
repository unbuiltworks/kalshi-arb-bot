# GEMINI_ECON_V1 — first prospective windows

**2026-09-20, ~13:00–13:20Z.** Early. Nothing here is a verdict; it is the
first evidence the arm produced, recorded before it is acted on.

## The arm runs, and it refuses everything

```
ECON GEMI-BTC05M2609201305-UP GEMINI_ECON_V1 spec=FROZEN_DEFAULT
  entries 0/50  refused={'FLOW_REVERSIBLE': 50, 'NOT_CONCENTRATED': 2, 'SPREAD': 36}
  firstFills=0  compOpp=0  acceptable=0
  veto=0  defect_above_ceiling=0  primary=None
  div={'ADAPTATION_SUPPRESSED': 50}
```

Beside it, on the same tape and the same cycles:

```
WINDOW      (control) first=1 complete=1 matched=0.01 basis=1.20
WINDOW_ARM  EXEC_A_PLUS_B  first=1 complete=1 matched=0.01 basis=1.04
WINDOW_ARM  GEMINI_ECON_V1 rested=0 fills=0 first=0  basis=None  err=None
```

**The control completed at 1.20 and A+B at 1.04 — both losses against a $1.00
settlement — and the challenger formed neither.** `defect_above_ceiling=0`, so
the veto did not leak. That part is working as designed.

But `entries 0/50` is **starvation**, which is a preregistered FAILURE mode, and
the preregistration commits to reporting it rather than tuning it away. Before
it can be read as a refutation, the gates have to be shown to measure what they
claim to. Two of the three do not, yet.

## Why it refused, per gate

```
ECON_WHY GEMI-BTC05M2609201320-UP GEMINI_ECON_V1
  FLOW_REVERSIBLE  'only 0 prints in the window, below the minimum of 5;
                    flow direction is not yet evidence'
  SPREAD           'spread 0.30 above 0.08'
  NOT_CONCENTRATED 'a single print is 51.1% of window volume, above 45.0%
                    -- this is one participant, not liquidity'
```

### 1. FLOW_REVERSIBLE — mis-sourced, and mis-timed

**Zero prints**, not one-directional flow. Two distinct problems:

- **Provenance.** The features come from `habitat.public_flow`, whose docstring
  reads *"EVIDENCE ONLY: no price, size or decision is taken from this."*
  Gating an entry on it takes a decision from it. This is the same class of
  invariant as `contract_depth`'s *"no caller may price, size or gate anything
  on it"*, which already corrected the depth gate into a quote gate earlier on
  this branch.
- **Timing.** That tape is thin or empty **early in a window** — which is
  exactly when BTC5m `ENTRY` runs (tte 300→120). So the gate is structurally
  closed during the only period it is asked.

The correct source already exists: `l5_shadow.gemini_ws` carries real trade
events (`reason == "trade"`) with millisecond venue timestamps, and its
`prints_since` returns `None` rather than `[]` when it cannot vouch for an
interval — the exact distinction a reversibility gate depends on. That is the
tape the event-driven lane reads, and sourcing this gate there is a
**correctness fix, not a tuning change**.

### 2. SPREAD — the bound is wrong for this venue

`max_spread` was preregistered at **0.08** for BTC5m. Observed spreads on these
contracts reach **0.30**. The earlier book read (yes 0.60/0.65) was 0.05, so the
distribution is wide and the preregistered bound sits near its bottom.

This one is an honest preregistration error: the bound was set from a single
observed book rather than from a measured distribution. **It has not been
changed.** Moving it now, after seeing it bind, would be fitting a threshold to
the window that exposed it. The distribution should be measured from the
collector's own history first, and the new bound preregistered against it.

### 3. NOT_CONCENTRATED — working as intended

A single print at 51.1% of window volume is one participant, not liquidity.
This is the gate doing its job, and it fired on only 2 of 50.

## What is NOT being changed

No threshold was moved in response to any of this. `min_prints`,
`min_alternation` and `max_spread` remain the preregistered values. The flow
gates now carry `flow_source_provisional=True` so their output reads as
provisional evidence about the gate rather than a measurement of the market.

## Next change, and the order matters

1. Source the flow features from `gemini_ws` (correctness).
2. Then measure the spread distribution from collected history and preregister
   a bound against it (calibration).
3. Only then read the entry-admission rate as evidence about the market.

Doing (3) before (1) and (2) would report a gate defect as a market finding.
