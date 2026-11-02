# Inventory-aware DCA: NO-GO on both variants

**Date:** 2026-11-02 · Time-boxed evaluation · **Nothing deployed, nothing armed**

**Verdict: NO-GO on changing live policy.** Same-side DCA is structurally
EV-negative. Complement-first is **not a new policy** — the engine already
makes those decisions 145 times a campaign — and its value is blocked by one
unresolved measurement, not by policy.

All counterfactual figures below are **SIMULATED COUNTERFACTUALS** on
decision-time data only. The confirmed historical baseline is the campaign's
realized **−$0.0755** over 7 windows (−$0.3603 across the 5 windows these
policies touch).

---

## Policy B — same-side DCA: NO-GO, decisively

Rule: add 1 contract to the stranded side (the residual cap of 3.0 against
observed residuals of 2.00–2.08 leaves room for ~1).

| window | side | resid | own px | actual | **B** | delta |
|---|---|---|---|---|---|---|
| 1415z | UP | 2.08 | 0.440 | +1.1648 | +1.7248 | **+0.5600** |
| 1430z | DOWN | 2.02 | 0.470 | −0.9494 | −1.4194 | **−0.4700** |
| 1445z | UP | 2.02 | 0.480 | −0.9696 | −1.4496 | **−0.4800** |
| 1500z | mixed | 2.00 | 0.500 | −0.8188 | −1.3188 | **−0.5000** |
| **total** | | | | **−0.3603** | **−0.9655** | **−0.6052** |

**Why it cannot work, independent of this sample.** A matched pair redeems
exactly $1.00; an unmatched contract redeems $1.00 or $0.00. Same-side DCA
adds **unmatched** inventory, so it cannot create a pair — only the complement
can. And the own price *is* the market's probability, so adding at market is
**EV-neutral before costs** and negative by the measured adverse selection on
residual inventory: **−$0.2315/unit over 384 windows**.

| | |
|---|---|
| expected cost, +1 contract × 4 windows | **−$0.9260** |
| realized counterfactual | **−$0.6052** |

Same sign, same order of magnitude. **The lower average cost is an accounting
artifact: it scales a bet whose expected value we have measured as negative.**

**Maximum additional downside.** At the cap, per-window residual rises
2.00→3.00, and the observed worst case moves from −$0.9494 to **−$1.4194**, a
**+50%** increase in per-window loss. The $5 daily halt would then bind after
**~3.5 windows instead of ~5.3**. Capital utilisation rises ~50% on the
residual leg for a negative-EV position, and the $1.50 per-market worst-case
deficit cap becomes the binding constraint sooner.

## Policy C — complement-first: not a new policy, and not yet decidable

Rule, no hindsight: complete at the **first** decision with
`pair_own_cost ≤ 0.96`.

| window | first T+ | pair | locked | actual | **C** | delta |
|---|---|---|---|---|---|---|
| 1400z | — | never ≤0.96 | — | +1.2127 | +1.2127 | 0 |
| **1415z** | 180 | 0.7400 | +0.5408 | +1.1648 | +0.5408 | **−0.6240** |
| **1430z** | 56 | 0.9600 | +0.0808 | −0.9494 | +0.0808 | **+1.0302** |
| **1445z** | 366 | 0.9000 | +0.2020 | −0.9696 | +0.2020 | **+1.1716** |
| 1500z | — | never ≤0.96 | — | −0.8188 | −0.8188 | 0 |
| **total** | | | | **−0.3603** | **+1.2175** | **+1.5778** |

**Three reasons this is not a GO, despite the headline.**

1. **It is already the deployed policy.** The engine returned
   `action: COMPLETE, ok: true` **145 times** across this campaign, 109 of
   them inside the 0.96 ceiling. C is not "adopt complement-first" — it is
   "make the existing complement-first decisions execute." That is the
   decision/execution gap, not a policy question.
2. **The prices may not be executable.** Every `locked_pnl_usd` is computed
   **before** the depth check, and the freshest economics refusal in 1430z was
   `COMPLEMENT_DEPTH_INSUFFICIENT`. A pair cost taken at top-of-book without
   depth for `need: 2.02` is not an executable profit. **This is the blocking
   unknown.**
3. **It is not statistically distinguishable from zero.** Three windows
   carried a qualifying decision. Per-window sd is 1.05, so the standard error
   of a 3-window total is **≈1.82**, and +1.5778 sits **inside one SE of
   zero**.

And 1415z shows the mechanism is not free: completing there would have cost
**−$0.6240**, because holding won. Same shape as every completion result in
this program — variance reduction that happened to pay in this draw.

## Answers

**Does same-side DCA improve expected net P&L, or merely lower average cost
while increasing exposure?** **The latter, definitively.** Expected −$0.9260,
realized −$0.6052, and structurally it cannot form a pair.

**Could complement-first have prevented actual stranded losses?** Nominally
yes in **2 of 4** (1430z +$1.0302, 1445z +$1.1716); it would have **cost**
−$0.6240 in a third. Net +$1.5778 nominal, executability unverified.

**Decision-time conditions where either is preferable?** For C:
`pair_own_cost ≤ 0.96` **and** displayed complement depth ≥ `need` — and the
second half is precisely what is not recorded. For B: **none found.**

**Maximum additional downside?** B: per-window worst case −$0.9494 → −$1.4194
(+50%), daily halt binding after ~3.5 windows instead of ~5.3.

**Sufficient evidence to change live policy?** **No.**

## Missing data, named

- **Complement depth at the completion price.** Not recorded against any
  `BOOK_COMPLETION` decision. Without it the +$1.5778 cannot be separated from
  an unfillable top-of-book quote.
- **Opposite-side depth at first-leg entry.** 0 of 39 live pre-submits carry
  depth fields.
- **The instrumentation that resolves the first gap is already written and
  tested** (`L5_BOOK_COMPLETION_RESULT`, commit `4d7f510d`, 14 tests, 2,834
  passing) and is **not deployed**. One window with unmatched inventory makes
  C decidable.

## Recommendation

**NO-GO on a bounded live validation of either policy.** B is EV-negative on
both measured and structural grounds and I would not validate it at any size.
C needs no validation — it needs the already-written measurement deployed, and
then the question answers itself from the next campaign's own tape.

No executor change, no DCA, no taker crossing, no risk-limit change, nothing
armed, campaign authorization untouched.
