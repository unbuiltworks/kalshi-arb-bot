# GEMINI_ECON_V1 — preregistration, written before the arm has produced a row

Fixed **2026-09-20**, before `GEMINI_ECON_V1` had observed a single live cycle.
Nothing below is to be re-chosen from the prospective sample it will be used to
score. Where a threshold is stated, the commit that states it predates the data.

---

## 1. What is being tested

> Can the L5 continuous-inventory mechanism, combined with Gemini-specific
> execution and economics, produce positive **executable** expectancy in BTC5m
> and BTC15m?

Not "can it fill more". The control already fills — `EXEC_A_PLUS_B` reached 21
matched pairs on BTC5m — and the deployed BTC15m arm completed a pair at a
basis of **1.13** against a $1.00 settlement. Fills are not the scarce thing.
Fills that pay are.

## 2. The arms, and which one is immutable

| arm | role | where |
|---|---|---|
| `CORRECTED_QUEUE_V1` | **IMMUTABLE CONTROL** | `gemini-maker-shadow`, serving `a44de245` |
| `L5_ADAPTED_GEMINI_BTC15M_V2` | timing correction, preserved | same service |
| `EXEC_A_PLUS_B` | execution adaptation A+B | same service |
| `GEMINI_ECON_V1` | **the challenger** | new service, this branch |

The control service is not redeployed, not restarted, and its `watchPatterns`
are not widened. The challenger runs in its own process, on its own state root,
reading the same public tape.

**The challenger inherits `L5_ADAPTED_GEMINI_BTC15M_V2` on BTC15m** rather than
the frozen default, for a reason that is mechanical and not a preference: the
default lane's `TTE_policy` is `[0, 300]`, which withholds the surface for the
entire 900→300 region, so a cohort policy operating there could never be
exercised at all. Inheriting it also makes the comparison the right one — the
challenger then differs from the deployed BTC15M_V2 arm in **economics and
nothing else**.

## 3. Preregistered thresholds

From `src/gemini_execution/econ/policy.py`, fixed in the same commit:

| | BTC5m | BTC15m |
|---|---|---|
| quote band (s) | `[0, 300]` | `[0, 900]` (the V2 correction, preserved) |
| ENTRY until TTE | 120 | **300** |
| MIXED until TTE | 60 | 120 |
| withdraw below TTE | 30 | 30 |
| max residual | 0.02 | 0.04 |
| toxicity horizon (s) | 20 | 60 |
| min alternation | 0.35 | 0.30 |
| max largest-print share | 0.45 | 0.50 |
| max required gap (ticks) | 2 | 4 |
| min prints | 5 | 4 |
| hazard threshold | 0.40 | 0.35 |
| min edge | 0.005 | 0.005 |
| max spread | 0.08 | 0.10 |

BTC5m is **stricter**, not merely faster: 300s leaves almost no room to recover
a complement, so a bad entry is carried to settlement rather than worked out of.

## 4. Primary metric

**Net cents per capital-dollar-minute**, per cohort, challenger vs control —
trading P&L net of actual fees, divided by capital-dollar-minutes. It cannot be
improved by trading more.

**Rebate is excluded from it.** `REBATE_APPLICABILITY_1.md` establishes that the
published maker rebate rounds DOWN at daily payment, so at probe size the
per-fill accrual is **$0.00**. Accrual is reported in its own field; only a
venue `AdminCredit` record may populate `rebate_credited`.

## 5. The honest sample-size problem, and the readout that survives it

The control produced **1** matched pair in 132 BTC5m windows and BTC15M_V2 **2**
in 43 BTC15m windows. Any criterion resting on matched pairs needs days.

So the **primary prospective readout is the admission side**, which accrues per
cycle rather than per pair:

```
complement_opportunities              the complement was QUOTED
acceptable_complement_opportunities   ... at or below the veto's ceiling
acceptable_share                      the ratio
entry_refusals_by_gate                which gate refused, counted
```

The gap between the first two is the lane's own doing and is the fastest
-accruing evidence we have about whether these markets can ever pay.

## 6. Success and failure, decided now

**SUCCESS** requires all of:

1. `PAIRS_ABOVE_CEILING_defect_counter == 0` across the whole run;
2. every matched pair fee-positive after actual fees, **rebate excluded**;
3. `PRIMARY_net_cents_per_capital_dollar_minute > 0` on the cohort judged;
4. at least **25 first fills** in that cohort before any verdict is read.

**FAILURE, to be reported as a result and not repaired away:**

- **Veto leak.** Any pair above the ceiling is a **defect**, not a result. The
  run is void for scoring and the defect is fixed before anything is claimed.
- **Starvation.** Fewer than 5 first fills in 100 windows means entry admission
  is too strict. That is a refutation and is reported as one, with
  `entry_refusals_by_gate` naming which gate did it.
- **Rebate disguise.** Economics that are positive only when accrued rebate is
  netted in count as FAILURE. This is the exact disguise the veto exists to
  prevent.
- **Fidelity laundering.** `ADAPTATION_ADDED > 0` means the challenger invented
  a rung L5 never proposed. A test pins it at zero; a nonzero value in
  production is a defect, and any resulting economics are an adaptation, never
  an L5 discovery.

## 7. The event-driven sub-experiment

Separate question, separate measurement, and **not** scored on fills.

The A+B replay found that of 1,031 decisions with a rung already at the front
of the queue, 824 requoted — every one because the touch had moved more than a
tick, none for a better queue. The recorded conclusion was that the touch
"commonly moves 2-7 ticks between 8-second cycles".

That is a claim about the **cycle**. So the measurement is:

> median touch movement, in ticks, **between consecutive decisions**

- **Confirms the hypothesis:** event-driven decisions see a median of 0–1 ticks
  where the 8s cycle sees 2–7. The cycle was the binding constraint.
- **Refutes it:** the two see comparable movement. The cycle was not the
  constraint, and front-of-queue execution is limited by something else.

Either answer is a result. Latency is recorded at both seams; in shadow
`decision → order` is measured to the instant an order *would* have been
emitted and is labelled as such — it is not a venue round trip.

## 8. Known limits, stated up front

- **No settlement-reference proxy exists in this build.** The lane has a public
  spot tape but no strike-relative reference, so `REFERENCE_AGREEMENT` records
  UNKNOWN on every row. It is listed **advisory** during SHADOW so the sample
  can accrue. `phase=LIVE` carries no advisory gates at all — the constructor
  empties them — so this cannot silently follow the strategy into production.
- **The hazard model is uncalibrated and says so.** It returns `p: None` until a
  cell holds 30 resolved observations. Uncalibrated never justifies *waiting*;
  it does not block *entry*, because refusing there would prevent collecting the
  outcomes that calibrate the cell.
- **Hypothetical fills are an upper bound.** Gemini serves no book history, size
  ahead is the displayed size at placement, and our own presence would have
  changed the flow being replayed. Unchanged from the control's own caveat.
- **Depth cannot gate anything.** `metadata.contract_depth` carries a standing
  "EVIDENCE ONLY" label and the contract object publishes no depth, so the
  two-sided test is a **quote** test, not a depth test.

## 9. Safety

SHADOW only. `mutations_sent` stays 0. No session, no signer, no order path in
any econ module — asserted per module by test. No live capital is armed from
this session, and `arming.EXPECTED_AUTH_ID` remains the spent constant
`OWNER-2026-09-17-GEMINI-CANARY-1`. Unrelated Polymarket sports and SOL work is
untouched.
