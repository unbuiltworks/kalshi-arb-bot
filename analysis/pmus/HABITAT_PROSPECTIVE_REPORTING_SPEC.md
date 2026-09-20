# Habitat selector — prospective read, reporting specification

**The selector is FROZEN.** `analysis/pmus/habitat_selector_frozen.json`,
sha256 `f9dcd886549a28656c315cd2249075f6aae7c6fcf48bd77e5a2fa0abfc861959`,
frozen `2026-09-20T19:52:46.731868Z` at commit `fd91d38e`. Verified byte-identical
at the time this spec was written.

Features, signs, weights, the tercile cutoff, the cohort definition, the success
condition and the exclusion rules **are not to be modified now that prospective
data has begun**. This file adds only *what must be reported*; it changes nothing
the selector does, and it is deliberately a separate file so the frozen artefact's
hash does not move.

---

## The preregistered condition

> **PASS** — SELECTED rows show materially lower `I_excess` than REJECTED rows on
> a cohort collected entirely after `prospective_start_epoch`, with the **same
> sign** and a **comparable effect** to the historical OOS separation of
> **−0.192**.

Historical OOS reference, for comparison only: selected `I_excess` p50 **+0.092**
(n=29) vs rejected **+0.284** (n=34), Spearman(score, `I_excess`) **0.315**,
eligible rate 46.0%.

The read is **PASS or FAIL against this condition**. It is not an occasion to
propose a new hypothesis.

---

## What the read must report

1. **Counts** — SELECTED and REJECTED row counts, and **distinct markets** in
   each. Rows are not independent; markets are the unit that matters.
2. **Realized `I_excess`** — p50 and mean for each arm, and the
   SELECTED-minus-REJECTED separation.
3. **Realized raw `I_fwd`** — reported **only** alongside its print-count and
   noise-floor context, never bare. See the canonical rule below.
4. **Two-sided prints per minute**, forward window, each arm.
5. **Print counts and effective sample sizes** — `n_fwd` distribution per arm,
   and the median noise floor implied by it.
6. **Selector rank against future `I_excess`** — Spearman over the whole
   prospective cohort, against the historical OOS 0.315.
7. **Uncertainty** — cluster bootstrap CI on the separation, resampling
   **markets**, not rows (`imbalance_sampling.cluster_bootstrap_ci`). Rows within
   a market share its book, its flow and its crowd.
8. **Missingness and classification failures** — markets with no book, rows
   dropped for thin feature or target windows, and the share of prints whose
   direction was **UNSUPPORTED** (inside the spread) and therefore excluded.
9. **Cohort adequacy** — if the cohort is too thin to carry the claim, say so and
   re-arm rather than reporting a number that cannot support a verdict.

---

## Canonical sampling rule — permanent

> **RAW IMBALANCE CANNOT BE COMPARED ACROSS UNEQUAL PRINT COUNTS.
> `I_excess`, OR MATCHED-SAMPLE INFERENCE, IS CANONICAL.**

Implemented in `analysis/pmus/imbalance_sampling.py` and locked by
`tests/test_imbalance_sampling.py`. A perfectly balanced market still measures
non-zero imbalance from finite flow:

| n | 10 | 18 | 30 | 50 | 100 | 1000 | 58312 |
|---|---|---|---|---|---|---|---|
| E[I] if truly balanced | 0.246 | 0.185 | 0.144 | 0.112 | 0.079 | 0.025 | 0.003 |

L5's 0.083 came from 58,312 fills (floor 0.003) — nearly all real. The same 0.083
in an 18-print window sits *below* the floor and is evidence of nothing.
`compare_raw()` raises rather than returning a number when the counts differ.

---

## Branches, both preregistered

**If it PASSES** — freeze the cohort, then run the **already-frozen** L5
maker-ladder engine (`analysis/pmus/ladder_replay.py`, R=4, band −2..+1, frozen on
L5 TRAIN) over it with **zero strategy retuning**. That is the first legitimate
prospective transfer test. Report economics line by line: completion,
residual:matched, basis, matched P&L, residual P&L, standalone return, and
attributable incentives — which remain **$0.00 confirmed** until a fill-level
attribution exists.

**If it FAILS** — the preregistered NO branch is honoured: **PMUS is killed as an
L5 deployment venue.** Do **not** search this cohort for another selector, another
cutoff, another feature set or another sub-cohort. Move this same habitat
measurement framework — `imbalance_sampling.py` plus the feature/target extraction
in `habitat_selector.py` — to candidate venues and categories, and ask where
balanced two-sided flow exists prospectively.

Under either branch: no orders, no live probe, no parameter changes.
