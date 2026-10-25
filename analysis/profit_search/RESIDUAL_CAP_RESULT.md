# Result: residual cap of 2 — NEGATIVE. The controller is unchanged.

Pre-registered in `RESIDUAL_CAP_PREREGISTRATION.md`, committed as `48bef67b`
**before** this evaluation produced a number. Nothing in the pre-registration
was altered after seeing results.

**Verdict: the hypothesis fails two of the three pre-registered criteria. The
engine's `max_resid_qty` default stays as it is.**

---

## 1. The pre-registered test, on its own terms: INCONCLUSIVE

Both venue-native datasets, chronological split, grid fixed in advance:

| cap | PMUS traded | PMUS net | Kalshi traded | Kalshi net |
|---|---|---|---|---|
| 1 | 0 | $0.0000 | 0 | $0.0000 |
| **2** | 0 | $0.0000 | 0 | $0.0000 |
| 3 | 0 | $0.0000 | 0 | $0.0000 |
| 5 | 0 | $0.0000 | 0 | $0.0000 |
| 10 | 0 | $0.0000 | 0 | $0.0000 |
| 50 | 0 | $0.0000 | 0 | $0.0000 |

Every arm WAITs, so the test says nothing about the cap. **This is exactly the
inconclusive case the pre-registration declared in advance** ("if every arm
returns $0.00 with zero trades, the test says nothing about the residual cap —
it says the probability gate binds first").

What binds has in fact changed, and for a reportable reason. With
`adverse_shift` now at the **measured** 0.0827 rather than the invented 0.015,
the refusal is no longer `PROB_UNCERTAIN` — the ledger census over 228 OOS
decisions is `EDGE_BELOW_BAR(0.06)` 169, `TOO_LATE_TO_EXTEND` 59, with **zero
candidates refused for uncertainty.** The controller now declines on
**economics**, not on ignorance. Opening the probability gate no longer makes it
trade, where before the correction it traded and lost −$10.38.

## 2. The diagnostic the pre-registration anticipated: every cap NEGATIVE

To make the cap's mechanical effect visible at all, the probability gate was
forced open so trading occurs. **This is not an edge claim** — it trades a
no-skill signal deliberately. What is informative is the *ordering across caps
under identical probability inputs*. Kalshi venue-native, 27 OOS windows:

| cap | windows traded | OOS net | per window | max DD | residual units |
|---|---|---|---|---|---|
| 1 | 0 | $0.0000 | — | 0.00 | 0.00 |
| **2** | **3 (11.1 %)** | **−$0.6605** | **−$0.2202** | 0.66 | 2.50 |
| 3 | 8 | −$1.2789 | −$0.1599 | 1.96 | 2.09 |
| 5 | 10 | −$1.3610 | −$0.1361 | 2.83 | 3.00 |
| 10 | 10 | −$3.0683 | −$0.3068 | 5.96 | 3.17 |
| 50 | 10 | −$10.2020 | −$1.0202 | 15.25 | 7.32 |

## 3. Against the pre-registered criteria

| criterion | result |
|---|---|
| OOS net at cap 2 is **positive** | **FAILS** — −$0.6605, and $0.0000 in the ungated test |
| positive on both datasets, or positive on one and not negative on the other | **FAILS** — negative on Kalshi, inconclusive on PMUS |
| cap-2 arm trades in ≥25 % of eligible OOS windows | **FAILS** — 3 of 27 = **11.1 %** |

## 4. What replicates, and what does not — the distinction that matters

**The stratification's DIRECTION replicates.** Net worsens monotonically as the
cap rises: −$0.66 → −$1.28 → −$1.36 → −$3.07 → −$10.20. "Accumulating residual
is the problem" is supported, and strongly.

**Its PROFITABILITY does not.** The original table showed |residual| 1–2
windows earning **+$0.3248 each with a positive residual component**. Here the
cap-2 *policy* loses $0.2202 per window. The two are not in conflict — they are
the confound the pre-registration was written to catch: windows **end** at 1–2
residual units partly because nothing went wrong in them. That is an outcome,
not a lever. Applying a cap prospectively changes which windows exist, and the
benign ones do not survive the change.

**So the family's optimum is its boundary.** If less residual is monotonically
better, the limit of the argument is *no* residual, which is cap 1 / cap 0 —
and both are **$0.0000 with zero trades**, i.e. WAIT. WAIT dominates every
member of the pre-registered grid, which is the same conclusion every other arm
in this issue has reached, now reached from the inventory side.

## 5. Why this was not a wasted test

It converts a plausible, well-motivated reading of a real measurement into a
rejected hypothesis with a stated mechanism, and it moved the engine's binding
constraint from `PROB_UNCERTAIN` to `EDGE_BELOW_BAR` by replacing an invented
constant with a measured one. The controller is **unchanged** in its risk rules
and execution assumptions, which is the correct outcome when the evidence does
not support a change.
