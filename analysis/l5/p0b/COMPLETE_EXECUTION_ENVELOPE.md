# P0-B — is the frozen L5 target price costing us COMPLETE fills?

**Answer: no. Measured across all 137 real live COMPLETE attempts, the number that had a legal AND sufficient one-tick improvement is ZERO.** The maker envelope is already exhausted. The binding constraint is not price selection.

Generator `analysis/l5/p0b/complete_execution_envelope.py` · 9 regressions · source: 72 `session_end` rows from the live campaign. **No production execution changed.**

---

## Two corrections to my own previous report

**1. I over-generalised from BUF-HOU.** I told you the COMPLETE missed "by ONE TICK" and implied that was the pattern. Across 137 attempts it is **7**. The typical attempt is **~60 ticks (30 cents) away** from the complement's best ask. Nothing about price selection fixes that.

**2. My first cut of this analysis had the tick size wrong, and it inverted a finding.** `orderPriceMinTickSize` reports `0.01`, but 98 of 137 live COMPLETE prices are half-cent and the venue range is `0.005..0.995`. With `TICK=0.01` a genuine $0.005 gap divides to 0.5 and `int()`-truncates to **zero**, which reported seven orders as *"the book reached our price and we still did not fill"* — a queue/latency conclusion. Corrected: **`gap <= 0` happens ZERO times in 137 attempts.** The book never once reached us. A test now pins it.

---

## The population

| | |
|---|---|
| COMPLETE attempts | **137** |
| filled | **4** (2.92%) |
| median rest | **2.02 s** (not 31 s — 118 of 137 rested 1–10 s) |
| dominant end reason | `repriced:touch_moved` (81 of 137) |

The re-quote loop **is** running on COMPLETE — 81 of 137 attempts were cancelled and repriced as L5's touch moved. This is worth stating because an older forensics note in the repo says COMPLETE was excluded from `touch_moved` repricing; that is no longer true on this build.

## Distance from the complement book

| bucket | attempts | filled |
|---|---|---|
| BOOK_REACHED_US (gap ≤ 0) | **0** | — |
| 1 tick short | 7 | 0 |
| 2 ticks short | 1 | 0 |
| **3+ ticks short** | **114** | 2 |
| unknown (no rest_book gap) | 15 | 2 |

Gap distribution is bimodal and brutal: **60 ticks (27), 59 (21), 68 (19), 64 (7)** — i.e. ~30 cents — against a small cluster at 6 ticks (16) and 1 tick (7).

`p_fill_before_cancel` overall **0.0292**. Horizon probabilities are reported with censoring: an order cancelled at 2 s is excluded from the 60 s denominator rather than counted as a failure, because it says nothing about 60 s.

## The one-tick counterfactual — all four tests

Of 137 evaluable attempts:

| test | passes |
|---|---|
| pair cost still ≤ 1.00 | 43 |
| still fee-safe (≤ 0.98) | 31 |
| still post-only (strictly inside the ask) | 74 |
| book would actually have reached it | **7** |
| **ALL FOUR** | **0** |

## Why zero — the mechanism

All 7 near-misses are two markets, and they are identical in shape:

```
aec-mlb-lad-mia   our px 0.520 = L5 touch 0.520   min_opp_ask 0.5250   pair 0.9950
                  +1 tick -> 0.525 = THE ASK      pair 1.0000  fee_safe FALSE  post_only FALSE
aec-nfl-buf-hou   our px 0.555 = L5 touch 0.555   min_opp_ask 0.5600   pair 0.9627
                  +1 tick -> 0.560 = THE ASK      pair 0.9677  fee_safe TRUE   post_only FALSE
```

**In every case our price was already exactly at L5's touch, and the ask sat exactly one tick above it.** The complement spread is one tick wide. "One tick more aggressive" *is the ask* — a marketable order, not a resting maker order. There is no room inside the maker envelope to move into.

So the separation you asked for resolves cleanly:

- **MODEL DECISION** (COMPLETE the YES side) — correct, and not in question.
- **EXECUTION POLICY** (rest at L5's touch) — already at the best maker price available. It is not being "treated as the literal price" to our cost; it happens to *be* the only legal maker price.

**An execution adapter that picks the highest-fill-probability price inside the economically legal maker range would have changed nothing on this tape.** The range has one element.

## What the evidence actually points at

1. **BUF-HOU is the one economically clean miss.** Pair at the ask would have been **0.9677** — inside the ceiling *and* fee-safe. The only blocker was maker-only. For reference, canonical L5 is **97.24% maker, `p_taker` ≈ 0.0276** — he is not purely a maker. A bounded taker completion, gated on comfortable fee-safe headroom, would be L5-*consistent* rather than a deviation. **I am not proposing it; it is a policy change and it is yours to decide.**
2. **The 4 MLB near-misses were never affordable** — pair at the ask is exactly 1.0000, over the fee-safe bound. The gates refused correctly.
3. **The 114 attempts at 3+ ticks are a different problem entirely.** A complement ask 30 cents away is not an execution defect; it is a market with no other side. That is market selection (P3/P4), not price selection.

## Not done

- No production execution change, no gate loosened, no limit moved.
- P(fill) by distance is thin where it matters: 7 and 1 attempt in the 1- and 2-tick buckets. Directionally clear (0 fills), statistically weak.
- 15 attempts lack a `rest_book` gap and are reported as `unknown` rather than assumed.
