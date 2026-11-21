# RECONCILIATION 001 — ECON_FUNNEL_MO vs ECON_TOX markout

**P0 only. No strategy rule, arm, threshold, capital or risk setting changed.
No general instrumentation added. The frozen collection ran throughout.**

---

## The row-level reconciliation cannot be produced, and here is exactly why

**THERE ARE ZERO RELIABLY OVERLAPPING ROWS, and no join that could identify
them.** Stated plainly rather than inferred:

1. **The two stores share no identifier.** `ToxicityDataset` rows carry
   `{cohort, arm, symbol, leg, price, t, features, outcome}`. `fill_sequence`
   rows carry `seq_id` and `observation_id`. **Neither store records the
   other's key.** There was no fill ID to join on, so the per-row table
   requested — observation/fill ID, comparison timestamp, age error, the two
   references, the two signed markouts — **cannot be assembled for any
   historical row.**

2. **The two stores cover different periods.** The toxicity dataset has
   persisted since `c42c9fe`; the fill ledger did not persist until
   `89d4d28` and was destroyed by two redeploys. The cohort that produced
   `YES n=48, markout_p50_60s +1.5` **no longer exists on disk.**

3. **The historical rows live on a Railway volume** this session cannot read.

So the reconciliation below is **mechanical, from the code that produced both
numbers**, and is pinned by ten tests that reproduce the defect
deterministically. It is not an inference from a log sample.

---

## OBSERVATION

```
ECON_FUNNEL_MO (cohort A)  YES n=48  markout_p50_60s = +1.5
ECON_TOX       (persisted) YES n=6   toxic_share     = 1.00
```
`TOXIC ≡ markout@60s < 0` for the leg that filled. On any shared row these
cannot both hold.

## CAUSE — a join across two different clocks

**There is exactly ONE markout computation in the system.** The toxicity label
does not compute a markout; it **copies** `markout_ticks_by_horizon_s["60"]`
off the fill_sequence row:

```python
PF.ToxicityDataset.label(
    tgt,
    markout_60s=(row.get("markout_ticks_by_horizon_s") or {}).get("60"), ...)
```

So a sign inversion, a different price field, a different horizon and a
different maker perspective were **all impossible**. The stores could only
ever disagree about **which rows they hold** — and they did, for three
compounding reasons in one loop:

**C1 — the join keyed on the wrong event.** The toxicity capture is stamped
`t = time.time()` in the cycle the fill was **consumed**. The loop keyed on
`t_candidate`, the cycle the rung was **admitted**. A rung must rest before
any print can reach it, so the fill is **at least one 8-second cycle later**,
usually many. Matching was allowed within **12 s**, so only fills arriving
within ~1.5 cycles of admission were ever labelled.

**That is the bias, and it runs in the worst possible direction.** A rung that
fills almost immediately is a rung run over by fast one-directional flow —
precisely the adversely selected one. **Slow, benign fills were structurally
unlabellable.** `ECON_TOX`'s cohort is therefore enriched for toxicity by
construction, which is exactly what `toxic_share = 1.00` reports.

**C2 — the nearest-match fallback could mislabel.**
`min(byt, key=lambda x: abs(x - key))` attached whichever capture was nearest
in time, so one fill's outcome could land on a **different** fill's features.

**C3 — the candidate map was never refreshed.** `byt` was built once, filtered
on `outcome is None`, and rows labelled inside the loop could be **relabelled**
by a later fill. Rounding `t` to 0.1 s also collapsed two captures in one
cycle to a single key, silently dropping one.

### The seven questions

| # | question | answer |
|---|---|---|
| 1 | Same rows? | **NO.** Joined on two different events with no shared identifier. Zero rows can be confirmed as shared. |
| 2 | Same horizon/sample? | **Yes** — the same `["60"]` field, when matched at all. |
| 3 | Same price field? | **Yes** — a single computation; the label is a copy. |
| 4 | Same maker perspective? | **Yes** — same reason. |
| 5 | Sign inversion? | **NO.** Structurally impossible. Pinned by an AST test asserting `label()` performs no arithmetic. |
| 6 | Non-random subset? | **YES — and this is the cause.** Enriched for fast, adverse fills. |
| 7 | Bug in either calculation? | **No bug in the markout. Three bugs in the join** (C1–C3). |

## WHICH MEASURE IS AUTHORITATIVE

**`fill_sequence` / `ECON_FUNNEL_MO`.** It is the sole computation; `ECON_TOX`
is a downstream copy over a biased subsample. `ECON_TOX`'s **features** remain
authoritative for what they are — a causal pre-fill snapshot — but its
**`toxic_share` is not a population statistic** and must not be read as one.

## AFFECTED HISTORICAL CLAIMS

| claim | verdict |
|---|---|
| "ECON_TOX shows YES fills are 100% toxic" (my counter-evidence last turn) | **WITHDRAWN.** A selection artifact. It is not evidence against the YES hypothesis. |
| "Two instruments disagree, so every leg-based conclusion is provisional" | **RESOLVED.** They never measured the same rows. No leg conclusion is void on this ground. |
| `ECON_TOX_WF` "INSUFFICIENT, 10 and 4 labelled fills" | **Stands, and is worse than stated** — those fills are a biased subsample, so the effective usable cohort for a toxicity model is **zero**, not 10. |
| Any toxicity model fitted on labelled rows | **None was.** The walk-forward refused below 16 and no threshold was ever fitted. Nothing downstream is contaminated. |

## WHICH CLAIMS SURVIVE

- **Cohort A's `YES n=48 markout_p50_60s +1.5`** — survives. It comes from the
  authoritative store over all first fills, not a subsample.
- **Cohort A's 48→24 / 140→0 completion split** — survives this audit
  untouched; it never involved the toxicity store. It remains **UNRESOLVED**
  for the separate reason in Packet 001: cohort B does not reproduce it.
- **Cohort B's `cand 16/16, rested 25/25, first 0/18`** — unaffected.
- **The fill model's side symmetry** (83 assertions) — unaffected.
- **Residual terminal economics** — unaffected; different code path entirely.

**Net effect on the money question: the contradiction is closed and it removes
a piece of apparent counter-evidence rather than supporting the hypothesis.
The YES/NO completion asymmetry is exactly where Packet 001 left it —
UNRESOLVED, not reproduced by cohort B.**

---

## What changed (measurement repair only)

1. `fill_sequence` rows now expose **`first_fill_t`** — the same
   `time.time()` value the toxicity capture is stamped with, in the same
   cycle.
2. `finish()` joins on **`first_fill_t` exactly**. No tolerance, no
   nearest-match fallback, one capture consumed per fill. A row that cannot be
   joined stays **UNLABELLED**.
3. Every label now carries **`fill_seq_id`** and **`join_method`**
   (`FILL_T_EXACT`); rows labelled before this carry neither and are reported
   as `LEGACY_TIMESTAMP_HEURISTIC` so the biased cohort can never be pooled
   with clean rows.
4. **`ToxicityDataset.reconcile()`** asserts, every window, that the stored
   markout still equals the fill row it claims to come from, printed as
   `ECON_TOX_RECONCILE`. On disagreement it prints `*** DISAGREEMENT ***` and
   says every leg-based conclusion is void.

**It cannot fail while the label is a copy — that is the point.** It converts
an invariant we were relying on into one the evidence report asserts rather
than assumes, and it will fail loudly the moment anyone introduces a second
markout computation.

**Not changed:** admission rule, prices, cadence, thresholds, arms, queue
model, fill model, risk, capital. `entry.py`, `policy.py`, `veto.py`,
`actions.py`, `hypothetical.py`, `tape_replay.py`, `continuous_probe.py` and
`l5_surface/engine.py` remain byte-identical to the freeze.

## Reproduction
```
pytest tests/test_gemini_tox_funnel_reconciliation.py -q   # 10 tests
```
The legacy join is reimplemented verbatim inside the test file, so the bias is
demonstrated against real code rather than asserted from memory.

## STOP for review.
