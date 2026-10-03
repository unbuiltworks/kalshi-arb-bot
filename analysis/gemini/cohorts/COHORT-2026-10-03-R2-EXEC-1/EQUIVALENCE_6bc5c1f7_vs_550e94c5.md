# R2 rule equivalence artifact — kept permanently with the cohort evidence

The rule's arithmetic was written INLINE inside `extract()` when it was frozen
at **`6bc5c1f7`**, which made it unreachable from a live executor — and the
executor is forbidden from reproducing the signal logic. Those two constraints
cannot both hold, so at **`550e94c5`** the arithmetic was factored into five
module-level pure functions (`sigma_1s_from_ticks`, `favored_side`,
`executable`, `z_of`, `qualifies`) and `extract()` now calls them.

- **Semantic reference SHA:** `6bc5c1f730dd0d4639eb4c9459803dbf9d68dee5`
- **Executable fingerprint:** `69adbd18bd12d7cf8a1d04b248b85c12bbfe2ececfc51f6c7ef755ae8ad94569`
- **Pre-refactor source, preserved verbatim:** `r2_rule_pre_refactor_6bc5c1f7.py`
  (recovered with `git show 6bc5c1f7:analysis/gemini/info_edge/r2_frozen.py`)

## The proof

Both files were run against the **same tapes**, and their full instance sets
and R2 hit sets were compared field by field — ticker, TTE, side, price level,
price, z, correctness, size and depth:

```
DEV          instances old=142 new=142 identical=True | R2 hits old=16 new=16 identical=True
PROSPECTIVE  instances old=376 new=376 identical=True | R2 hits old=65 new=65 identical=True

EQUIVALENCE: PROVEN
```

Nothing was added, removed or retuned. The refactor changes **how the same
arithmetic is reached**, not what it computes.

`r2_frozen_result.json` moved only in `eligible_windows` (134 → 140) and
`instances_examined` (366 → 376), which is newly collected data rather than a
rule change: **every R2 statistic is unchanged**.

The fingerprint changed because the file's text changed. That is expected and
is not drift; the old value `64b178378806b5c257b58b29d0ba49da` must not be
quoted as current.
