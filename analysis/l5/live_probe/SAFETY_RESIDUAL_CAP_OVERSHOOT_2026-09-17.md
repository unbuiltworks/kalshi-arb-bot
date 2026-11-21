# SAFETY ISSUE — residual cap overshoot (RECORDED, NOT REPAIRED HERE)

Owner instruction: record this separately and **do not** combine its repair with
the selector deployment. Nothing in this document is fixed by that deployment.

## What was observed

Live on `aec-itfme-adrbar-fautab-2026-09-17`, the refusal string was:

```
residual_cap:3.8636363636363638>=3
```

The configured cap is **3 behavioral units**. The residual reached **3.8636**,
an overshoot of **0.8636 units (≈29% above the cap)** before the cap began
refusing.

## Why it happens (mechanism, not yet a fix)

The cap is evaluated **before** proposing a further same-side EXTEND:

```python
elif relation == "extend" and abs(inv.behavioral_residual()) >= max_residual_units():
    relation, refusal = "hold", f"residual_cap:{...}>={...}"
```

So it is a **pre-trade admission test on the NEXT action**, not a bound on the
resulting position. A fill that lands while residual is below the cap can carry
the position past it: the last admitted EXTEND was sized by the ordinary sizing
path, not by the distance remaining to the cap. The cap therefore behaves as
"stop starting new same-side legs once at/over 3", not "never exceed 3".

Contributing: the 4 fills were 0.22 + 0.21 + 0.21 + 0.21 = 0.85 shares, and the
behavioral unit divides that into 3.8636 units — the final admitted leg crossed
the boundary rather than stopping at it.

## Why it did not lose money here

The overshoot did not breach any **monetary** control. Spend was $0.3783 against
a $5.00 per-market cap and a $15.00 campaign cap. `venue_vs_ledger` agreed to
delta 0.0000 throughout. The $6.00 daily stop was never approached.

## Why it still matters

The residual cap is the control that bounds **directional** exposure, which is
the exposure that produced both realized losses to date. A cap that can be
exceeded by ~29% is a weaker guarantee than its number implies, and the gap
scales with leg size — at a larger rung the same mechanism would overshoot by
more in absolute terms.

## Interaction with the selector defect (worth stating, still separate)

Under `modal` the overshoot was also a **deadlock**: EXTEND was refused by the
cap and COMPLETE was never selectable, so the position could only hold to
settlement. The stochastic selector removes the deadlock — a drawn COMPLETE is
not captured by the cap branch, which guards on `relation == "extend"` — but it
does **not** repair the overshoot. The cap will still admit a leg that crosses
the boundary. Do not read the selector fix as closing this.

## Not repaired here

No change is proposed or made in this deployment. Candidate repairs (for a
separate authorized change) include sizing the final admitted leg to the
distance remaining to the cap, or evaluating the cap against the
post-fill projected residual rather than the pre-trade one. Either alters
sizing or admission and needs its own review.
