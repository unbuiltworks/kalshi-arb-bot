# READ THIS BEFORE QUOTING `btc15m_segments.json`

**The TTE buckets in `btc15m_segments.json` bucket WINDOWS BY THE TIME-TO-EXPIRY
OF EACH WINDOW'S *FIRST* FILL. They do not count fills by time-to-expiry.**

This has already caused one wrong conclusion and the correction is recorded here
so it cannot be re-derived.

## The misreading

`segments.buckets["<60"].windows == 0` was read as "L5 places no fills in the
final 60 seconds". That is **FALSE**.

## The truth, measured from `analysis/l5/l5_canonical_sequence.csv.gz`

| quantity | value |
|---|---|
| fills in the final 60 s, all assets/durations | **4,261** |
| fills in the final 60 s, BTC15m only | **2,652** |
| fills in the final 120 s, all | **11,570** |
| median fill TTE, all | 377 s |
| median fill TTE, BTC15m | 431 s |

`<60: windows = 0` correctly means **no window's FIRST fill arrives in the last
60 seconds** — L5 never *initiates* a window that late. He is active there:
at <60 s to settlement his median EXTEND is 12 shares at a median price of 0.15.

## The second misreading in the same file

`complement_rate = 0.9838` is **the rate inside the `>600 s` first-fill bucket
only** (487 of 495 windows). Across **all 556 BTC15m windows** in the corpus the
two-sided rate is **0.9335**. Do not quote 98.4% as a population figure.

## Populations in this corpus — four different numbers, often confused

| n | population |
|---|---|
| **2,216** | all windows in `l5_canonical_sequence.csv.gz` (every one carries a settlement outcome) |
| **1,779** | windows containing ≥1 heavy-side EXTEND (the other 437 contain none). **Not** "the settled subset" |
| **556** | BTC15m windows |
| **527** | windows retained by `segments.py` after dropping 3,380 duplicate and 562 no-side rows |

## And the corpus is four days

Maker/taker classification exists only for **2026-08-28 → 2026-08-31**
(401 / 567 / 558 / 474 windows). March (88 windows) and April (128) are entirely
`maker_taker = unknown`. Any statement about maker share, fees or rebates is a
four-day statement.

Full verified analysis: `analysis/l5/next_action/L5_DECISION_POLICY.md` and
`analysis/l5/next_action/MAKER_TAKER_GAP.md`.
