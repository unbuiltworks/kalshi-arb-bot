# P0-C/D/E/F — what separates completion from stranding

Corpus: live campaign logs **2026-09-13 12:08Z → 19:54Z**, 220 sessions, 61 markets, 58 filled orders, 263 COMPLETE attempts, **14 markets that acquired inventory**, and **26 venue-confirmed REALIZED settlements**. Nothing deployed; no gate or limit touched.

**Sample honesty first.** 14 markets with fills cannot support conditional completion probabilities across the fourteen dimensions requested. Cutting that n by price band × spread × depth × flow × TTE × type would produce cells of 0–2 markets and invented precision. What the corpus *does* support is one very clean split, reported below. Railway log retention is the binding limit — the full ledger (9,055 orders) lives on the probe volume.

---

## P0-C — the split is EXTEND, not first-fill quality

**P(any match | first fill) = 9/14 = 0.643.** But the interesting cut is not price band or spread:

| cohort | markets | carry residual | COMPLETE attempts | COMPLETE fills |
|---|---|---|---|---|
| **0 extend fills** (+ ≥1 complete attempt) | **3** | **0 of 3** | **1 each** | **1 each — 3/3** |
| **≥1 extend fill** | **9** | **9 of 9** | 9,10,15,18,22,31,32,36,**87** | **14 of 260 (5.4%)** |

Every market where we did not extend and attempted a complete, **completed flat on the first attempt**. Every market where we extended **carried a residual**, and its complete attempts exploded while its fill rate collapsed.

### The mechanism is arithmetic, not correlation

EXTEND raises the held side's average cost, which shrinks the complement headroom under the 1.00 pair ceiling:

| market | extend fills | held avg cost | max complement for pair ≤ 1.00 | COMPLETE attempts / fills |
|---|---|---|---|---|
| sapole-salvas | 3 | 0.3404 | **0.6596** | 9 / 2 |
| lal-lev-fcb | 3 | 0.3548 | 0.6452 | 22 / 3 |
| mzp-ezt | 6 | 0.3754 | 0.6246 | 31 / 2 |
| lad-mia | 5 | 0.4217 | **0.5783** | **87 / 0** |
| jayfri-maxbas | 3 | 0.4604 | **0.5396** | **10 / 0** |

The two markets with **zero** complete fills have the **highest** average cost and the **least** headroom. The ceiling is doing its job — it refuses to overpay — but by then EXTEND has already priced the completion out of the market.

**Answer to "are our first fills wrong, or our inventory management?" — the inventory management.** The first fills reach a completable state; EXTEND then walks the average cost up until no complement the market will actually sell is affordable.

**The confound, stated plainly:** the 3 clean markets completed on their *first* complete attempt, so they never reached a state where extending was possible. Direction of causation for the 0-extend group is ambiguous. The mechanism in the 9-market group is not ambiguous — rising average cost shrinking headroom is arithmetic.

---

## P0-D — the taker counterfactual. Not a general fix.

Two cohorts, never merged, because **no assumed fill on a stale ask**: `authenticated_age_s` has a median of 6.2 s and a **p90 of 402.7 s**, so only 41 of 263 attempts carry a book fresher than 5 s.

| cohort | n | markets | median pair at taker | ≤1.00 | ≤0.98 |
|---|---|---|---|---|---|
| **STRICT** (ask ≤5 s old) | 41 | 9 | **1.0000** | 65.9% | 46.3% |
| **REST_WINDOW** (best ask seen while resting — an upper bound) | 218 | 12 | **1.1352** | 27.5% | 19.3% |

**The median taker completion is break-even at best (strict) and 13.5 cents above par (upper bound).** Crossing indiscriminately would lose money — consistent with P0-B's finding that the complement ask is typically ~60 ticks away.

There is a real selective subset:

| threshold | cohort | opportunities | markets | capital | matched profit **PRE-FEE** |
|---|---|---|---|---|---|
| pair ≤ 0.98 | strict | 19 | 3 | $17.55 | **$1.8326** |
| pair ≤ 0.98 | rest_window | 42 | 7 | $29.13 | **$9.0470** |
| pair ≤ 1.00 | rest_window | 44 | 7 | $31.31 | $9.0913 |

Against a book that has **realized −$23.3014** across 26 confirmed settlements (6 winners +$12.18, 20 losers −$35.48, worst −$6.2855), $1.83–$9.05 pre-fee is material — but it is **pre-fee, upper-bounded, and mostly unsettled**.

**Reported as UNKNOWN, not modelled:**
- **Fees.** The venue reports no per-fill fee on these rows. `feeCoefficient` 0.06 exists on market metadata but its application rule is not source-confirmed. **Net is UNKNOWN.** A zero-fee assumption would turn a marginal edge positive on paper.
- **Depth.** No ask size on these rows, so full-clip fills are not established. These are *opportunity* counts.
- **Residual avoided.** Only **2 of 7** taker-opportunity markets have a venue-confirmed settlement (mzp-ezt −$2.7322, lal-lev-fcb +$2.0380). The rest is unmeasurable today.
- The edge grid is partly degenerate by construction: edge = 1 − pair, so `pair ≤ 0.98` ⟺ `edge ≥ 2c`. Rows repeat for that reason, not from a bug.

---

## P0-E — when does L5 cross? **UNKNOWN from public data**

The public activity feed carries **no liquidity flag** — maker/taker is not published per fill. The 2.76% figure is a modelled `p_taker` from the frozen fidelity artifact, not an observed count, so it cannot be conditioned causally on TTE, residual or pair economics from the tape.

What the artifact does expose, keyed by TTE bucket: `tte>10m` → `p_taker` **0.0298** (n=8,160) for extend/complete states, and **0.024** (n=125) for the pre-fill open state. Directionally L5's crossing is slightly *more* likely once inventory exists — consistent with selective crossing to complete — but this is the model's own parameter, **not independent evidence**, and I will not present it as proof that L5 crosses to complete residual.

---

## P0-F — continuous inventory, at market level

Per market: fills, OPEN/EXTEND split, COMPLETE attempts and fills, peak matched, terminal residual — the table in P0-C. The pattern across the 9 extending markets is the same shape every time: OPEN → EXTEND(×1–6) → many refused COMPLETEs → residual to settlement. Terminal residuals ranged **−9.60 to +8.58 contracts**.

Full `qty(t)` / `avg_cost(t)` per-decision series exist in the decision rows and are reconstructable; they are not rendered here because the market-level split already answers the question the trajectories were meant to test.

---

## Where this points — not a decision, an input

Against your A/B/C/D menu, the evidence favours **C (continuous inventory handling)**, with **A** second and **B** narrow:

- **C** — EXTEND is what converts a completable position into a stranded one, via average cost against a fixed ceiling. 9/9 vs 0/3 is the strongest signal in the corpus.
- **A** — first fills are reaching completable states; the admission gates are not obviously the defect.
- **B** — a bounded taker path has a real but small, pre-fee, upper-bounded subset, and would not have helped the 114 attempts sitting 3+ ticks away. It does not address the mechanism.

**I am not choosing.** n = 14 markets, fees UNKNOWN, and 5 of 7 taker-opportunity markets unsettled.
