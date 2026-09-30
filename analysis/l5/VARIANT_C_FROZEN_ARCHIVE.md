# VARIANT C — FROZEN AND ARCHIVED

**Status: CLOSED. Do not tune. Do not re-validate against OOS49.**

Variant C is the inventory-aware complement controller: a maker complement
capped at `theta - economic_basis(held)` on the deficient side only, plus refusal
to EXTEND the long side while its complement is outstanding. Frozen engine code
at `967cf1ee`, independently reviewed and verified there. Held-out validation run
exactly once at `1474f726`.

## Result of record

| metric | baseline | variant_c |
|---|---|---|
| mean net | −0.0676 | −0.0644 |
| median net | +1.0905 | +0.6106 |
| matched-pair P&L / window | +1.6024 | **+2.6669** |
| residual P&L / window | −1.3457 | **−2.4603** |
| fees / window | 0.3243 | 0.2710 |
| sourced incentive / window | +0.2668 | +0.1205 |
| **realistic net / window** | **+0.1993** | **+0.0561** |
| worst window | −18.8000 | **−9.5000** |
| losing windows | 19/49 | 20/49 |
| ≥$5 windows | 9/49 | 5/49 |
| completion rate | 0.9478 | 0.8614 |
| fully completed | 30/49 | 10/49 |

Artefacts: `analysis/l5/r8_complement_controller_frozen.json` (training 25),
`analysis/l5/r8_complement_controller_oos.json` (held-out 49).

## What it settled

**Transferred:** the tail bound. Worst window halved, −$18.80 → −$9.50, out of
sample, and matched-pair economics improved +$1.06/window. The mechanism the
trace identified was real and the correction addressed it.

**Did not transfer:** net. The pair gain is handed back in residual (−1.35 →
−2.46), so mean is a tie and every completion metric falls. Capping the
complement stops us BUYING pairs above $1; it does not make anyone SELL us the
complement at the capped price.

**The durable finding:** the two failure modes are not independent. They are one
trade-off — expensive completion versus stranded residual — and the baseline
already sat near the better side of it. Any further gain has to come from making
the complement actually FILL at an acceptable price, not from refusing it.

## Two caveats attached to these numbers permanently

1. Interval volume is attributed wholesale to a non-touch queue level, which can
   only CREATE fills. Variant C's completion and fill figures are therefore
   optimistic, and completion still fell.
2. The pair-cost invariant holds for `variant_c`, not for `complement_controller`
   without `extend_requires_balance` (32 same-tick violations in that arm, max
   overshoot $0.0286/share; zero under variant_c).

## Cohort status

**OOS49 is CONSUMED.** It was spent as the single held-out validation of Variant
C. From `1474f726` onward it is DIAGNOSTIC data only: legitimate for
understanding mechanisms, never again evidence that a strategy generalises. Any
V1 validation must use forward windows that did not exist when V1 was written.

The frozen 25 remain the development cohort.
