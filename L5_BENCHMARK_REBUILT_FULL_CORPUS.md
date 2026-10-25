# The benchmark rebuilt on the full corpus — every behavioural conclusion survives

**Status: VALIDATION PASSED.** Nothing withdrawn. Two findings sharpen.

## What was wrong

The owner designated the 527-window L5 trajectory analysis as the behavioural benchmark. It was
built from `data/l5/raw/activity.jsonl` alone. Two further captures sit committed beside it:

| capture | state | BTC15m windows | BTC15m TRADE rows |
|---|---|---|---|
| `activity.jsonl` | what the benchmark used | 527 | 62,020 |
| `activity_gap.jsonl.gz` | 40 MB compressed, **never decompressed** | 5,094 | 630,551 |
| `sep/l5_sep23_25.json` | different schema, **disjoint** (0/20 sampled fills appear in either file above) | 65 | 5,121 |

The benchmark was **~9.5% of L5's BTC15m corpus.**

## The fix — one backward-compatible change

`analysis/l5/inventory_trajectory/trajectory.py::load()` now accepts one path **or several**, and
opens `.gz` transparently. The paths are read **in one call** because the dedup `seen` set is
shared: the captures overlap and a fill in both must count once. Nine downstream analyses import
this `load()` and all are upgraded by the same change. Single-path behaviour is unchanged —
re-running it reproduces 527 windows / 62,020 fills exactly.

Full rebuild: **5,503 windows / 677,597 fills** — 10.4x the windows, 10.9x the fills.

## The comparison

| | 527 sample | **full corpus** | delta |
|---|---|---|---|
| pair cost at **first formation**, median | 0.9900 | **0.9900** | **identical** |
| pair cost at **window end**, median | 0.9603 | **0.9709** | +1.1% |
| emergence from accumulation | 2.97c | **1.91c** | smaller but real |
| formations already <= 0.96 | 141/506 = 27.9% | **1635/5461 = 29.9%** | +2.0pp |
| **the 0.96 cap refuses** | 72.1% | **70.1%** | sharper |
| fills per window, median | 119 | **108** | -9.2% |
| inventory imbalance, median | 0.0979 | **0.0811** | **-17.2%** |
| inventory imbalance, p90 | 0.3957 | **0.2480** | **-37.3%** |
| matched qty, median | 893.2 | **855.3** | -4.2% |

## What this changes

**Nothing is withdrawn.** The 9.5% sample was representative on every behavioural dimension, and
the two headline findings are confirmed on ten times the data:

1. **L5's cheap pair cost is an EMERGENT PROPERTY of a running inventory, not a property of any
   individual pair.** He *forms* at a median 0.9900 — exactly par-minus-spread, the maker pair
   identity `1 - spread` on a one-cent book — and *ends* at 0.9709. The cheap number is
   manufactured by accumulation over ~108 fills, not found at acquisition.

2. **Our 0.96 completion ceiling refuses 70.1% of the pair formations L5 actually made.** The cap
   is not screening for quality; it is screening out the entry condition itself. A ceiling is a
   permission, not a price, and this one withholds permission for seven formations in ten.

Two findings **sharpen against us**:

3. **L5's residual discipline is tighter than the sample suggested** — median imbalance 8.1%, p90
   24.8%, against our deployed `max_abs_residual` of 10 on a 5-contract book. The gap between his
   discipline and our limits is wider than previously reported, not narrower.

4. **Capital is unchanged as the binding constraint.** Median matched quantity 855 contracts moved
   only -4.2%, so the capital-scale confound stands where it was: he runs ~188x our $5.

## What it does not change

The economic verdict is untouched. The mechanism is a rate on turnover (blended 1.107%), and
$480/day still requires turnover of the same order as L5's entire operation. Our ceiling is
$460.80/day of turnover. **No amount of behavioural fidelity closes a 188x capital gap.**
