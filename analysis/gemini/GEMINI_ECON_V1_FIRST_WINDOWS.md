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

---

# Update — WS re-sourcing live, 2026-09-20 ~13:44–13:47Z

## The re-source worked, and it is visible in the refusals

```
ECON_WHY GEMI-BTC05M2609201345-UP  {'SPREAD': 'spread 0.16 above 0.08',
                                    'FLOW_REVERSIBLE': 'only 1 prints ...',
                                    'NOT_CONCENTRATED': '100.0% of volume ...'}
ECON_WHY GEMI-BTC05M2609201350-UP  {'SPREAD': 'spread 0.23 above 0.08'}
```

Two things changed against the polled tape:

- the socket sees **1 print** where the poll reported **0** — and the refusal is
  now a measurement rather than an absence;
- in the second window **`FLOW_REVERSIBLE` does not appear at all** — the flow
  gates **passed**. `SPREAD` is the sole remaining blocker.

The 120-second socket warm-up predicted before deploy is visible too: early
cycles carry `flow_bucket: unknown` with `flow_source: UNVOUCHED`, because a
socket that has just connected cannot vouch for a trailing 120s window. Those
rows pool into their own cells and never mix with measured-flow cells.

## The spread distribution, measured rather than assumed

```
BTC5m|121-300|0.56-0.75|unknown  n=12  med=0.230  p90=0.260
BTC5m|121-300|0.46-0.55|unknown  n=2   med=0.220
BTC5m|121-300|0.76-0.90|thin     n=2   med=0.195
BTC5m|121-300|0.56-0.75|unknown  n=2   med=0.315   (second window)
```

**Typical BTC5m spread is 0.20–0.32 against a preregistered bound of 0.08** —
the bound is roughly 3–4× too tight. That is now a distribution rather than the
single anecdote (0.05) it was set from.

**It is still not being moved.** `cells_sufficient = 0`: no cell has reached the
preregistered floor of 30 observations. The instruction was to measure first,
and the measurement has begun, not finished.

## An early markout signal, flagged and deliberately under-read

Touch movement beside each observation:

```
mk = {'8': 1.0, '30': 6.5, '60': 14.5}     n=12
```

The touch moves about **1 tick in 8 seconds**, ~6.5 in 30s, ~14.5 in 60s.

That bears directly on the event-driven hypothesis, and it points the *opposite*
way to the A+B replay's "the touch commonly moves 2–7 ticks between 8-second
cycles". If the touch really moves ~1 tick per 8s here, the 8s cadence is much
less of a constraint than that replay implied, and the case for event-driven
decisions weakens rather than strengthens.

**n is 12.** This is a signal to test later under the preregistered
event-driven comparison, not a finding. It is recorded now so it cannot be
discovered after the fact and told as though it had been predicted.

## Still not done, in the agreed order

1. ~~re-source flow from the socket~~ — **done**, running at the unchanged 8s
   cadence
2. accumulate the spread census to `cells_sufficient > 0`, then preregister a
   treatment — **in progress**
3. hard-veto vs inventory-EV completion — the counterfactual ledger is live and
   opens a fork at every veto; no fork has resolved yet
4. entry admission
5. event-driven challenger — **off**, and a test asserts neither `shadow_soak`
   nor the arm reaches `event_driver`
