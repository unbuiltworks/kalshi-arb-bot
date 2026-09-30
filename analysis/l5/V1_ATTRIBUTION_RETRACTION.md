# RETRACTION — the V1 recoverable-dollar attribution

**The recoverable-dollar figures in `V1_RESIDUAL_DIAGNOSIS.md` and
`V1_FIVE_DOLLAR_DECOMPOSITION.md` are WITHDRAWN.** An independent adversarial
review found a blocker; I reproduced it and it is worse than reported. The
mechanism *ranking* changes, and the mechanism I scored zero and called
unmeasurable outranks the two I ranked first and second.

## The defect

`attribute()` ends with `recoverable if recoverable > 0 else D(0)`. That single
clip makes the whole quantity **outcome-conditioned**:

- 21 of 39 stranded windows were credited. **Every credited window is one where
  the residual lost.** In the 5 windows where the residual *won*, a persistent
  complement would also have filled — and **cost** money — and those are imputed
  exactly zero.
- **All 21 credited windows were credited MORE than they lost** (0245z lost
  $4.20, credited $7.82). The figure books fresh pair profit on top of loss
  avoided, so it is not "recoverable loss" as labelled.

Two further errors compound it:

- **Cross-episode pricing.** `best_own_terms_seen` is the minimum over *every*
  remaining tick. 25 of 39 windows return to flat after `i0` and 24 flip side, so
  for 12 of 14 credited windows the credited price comes from a different
  residual episode — sometimes the opposite side, where the action would have
  made the stranded residual *larger*.
- **Mismatched basis and quantity.** The formula pairs `basis_at_requirement`
  with the *final* residual quantity, and `basis_end` is dead code
  (`Book.settle()` never returns `resid_basis`).

## Corrected ranking, outcome-blind

| rank | mechanism | windows | $/window | status |
|---|---|---|---|---|
| **1** | **C′ reactive fee-safe TAKE** | 10–15 of 49 | **+0.79 to +1.26** | measurable, and I scored it **$0** |
| 2 | A persistent maker, strict (print at our exact level) | 5 | +0.680 | |
| 3 | A persistent maker, episode-coherent | 14 | **+0.082** | vs **+1.2872** published |
| 4 | B continuous inventory | 7 | **unscored** | formula charges nothing for the extra inventory it buys; one credit sits on a *winning* residual |
| 5 | D open economics | 1–2 | 0 | conclusion holds; `opens` under-counts pair formation 10 vs 40 |

C′ verified independently here: using the engine's **own** fee-safe marginal test
on the contemporaneous ladder, requiring full displayed depth for the entire
residual, counting winners and losers alike — **15 of 49 windows, +$1.2573/window**.

## What else falls

- **The scaling table is withdrawn.** `recoverable ∝ clip` is an algebraic
  identity (credit is `rq×(1−px)` with `rq = clip` and `px` fixed by hindsight),
  so q10→q50 growth is arithmetic, not a market finding. The "q67, ≈$330 peak"
  extrapolation extrapolates that artefact.
- **"Clip size is not the constraint"** was wrong as stated: the 14/14/14 came
  from testing three clips inside a single q10 trace. Real runs give 18/12/7.
- **The capacity dismissal was circular.** Interval volume is the *numerator of
  the fill model*, so "capacity is ample" and "A is fillable" were one assumption
  reported as two findings. Displayed takeable depth at or below our cap was
  **0 in 14 of 14 windows** at the credited fill.

## What survives, and it is the load-bearing part

- **Edge per deployed dollar**, independently reproduced and extended to q50:
  **+0.383% (q10) → −0.580% (q20) → −0.740% (q30) → −1.335% (q50)**, monotone in
  both arms. The reviewer could not break it. Absolute net worsens with clip
  regardless of denominator.
- **The final answer is unchanged and, if anything, understated**: this
  architecture has no path to $5/window at our capital. A collapses well below
  half face value, so my own stated hedge resolves to its pessimistic branch.
- **The oscillation measurement is NOT affected.** It is a forward simulation
  scored by `settle()` with no credit clipping and no hindsight price: both
  winning and losing residuals are counted. Its findings stand — 0.98 pairs per
  window at every clip, $0.18340 net edge per matched contract, and **51% of
  every contract acquired never pairs**.

## What this changes for V1

The reason I gave for prioritising persistent maker placement was
+$1.2872/window. That reason is gone. The case for it now rests on L5 provenance
and 5 strict windows, not on the dollars.

**V1 must carry a reactive fee-safe completion path measured alongside the
persistent maker, not deferred to "instrument only".** It is the best-evidenced
mechanism available, it uses the engine's existing θ-gated marginal test on real
displayed depth, and I filed it under "latency" and scored it zero.
