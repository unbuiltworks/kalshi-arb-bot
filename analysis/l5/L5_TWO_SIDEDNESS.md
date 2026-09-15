# Why L5 ends up two-sided and our clone ends up naked

Measured at the source, over L5's full canonical history: **2,216 windows,
1,610 of them two-sided.**

| | L5 | our clone |
|---|---|---|
| windows / positions ending **two-sided** | **72.7 %** | **0 %** |

That is the gap, quantified. Three measurements explain most of it, and two of
them point away from where we had been looking.

---

## 1. He gets there fast, and sometimes faster than we can see

| time from first fill to first opposite-side fill | |
|---|---|
| p10 | **7 s** |
| median | **54 s** |
| p90 | 166 s |
| max | 735 s |

| fills before the window is two-sided | |
|---|---|
| p10 | **1** |
| median | **2** |
| p90 | 5 |

**In the median window his second fill is already the other side.**

Set that against our observation lag of ~15 s. For the **fastest decile the
reactive clone is structurally too late** — the complement has already traded
before we can even learn of the first leg. For the median window we have ~39 s
of usable time, which is not hopeless.

So the reactive-vs-state-driven hypothesis is **partly** confirmed by L5's own
timing: it explains the fast tail, not the whole 72.7 → 0 gap. The arm R vs arm
S comparison now running will say how much of the rest it explains.

## 2. Our own pair discipline refuses half the pairs he actually makes

| L5's achieved pair cost | |
|---|---|
| p10 | 0.7667 |
| **median** | **0.9646** |
| p90 | 1.2223 |
| max | 1.7651 |

**Only 48.8 % of L5's own pairs come in at or under our 0.96 spring target.**
The target sits almost exactly on the median of his distribution — it bisects
the behaviour we are trying to reproduce.

| cohort | windows | two-sided | median pair cost | within our 0.96 |
|---|---|---|---|---|
| BTC15m | 527 | **96.0 %** | 0.9624 | 245 |
| BTC5m | 957 | 83.1 % | 0.9584 | 402 |
| ETH15m | 29 | 44.8 % | **1.2000** | 3 |
| ETH5m | 703 | 42.1 % | **1.0070** | 136 |

Two things fall out of this table:

- **BTC15m goes two-sided 96 % of the time.** It is by far the cleanest cohort
  for the behaviour we are trying to reproduce, and it is also where the
  minimum-size constraint bites hardest (18.1 % of orders below 5).
- **In ETH he routinely pays more than \$1 for a pair** — a guaranteed loss on
  the matched leg — and goes two-sided less than half the time. That is a
  different regime, and it is not the one the spring discipline was written
  for.

**This is a policy question, not a code change, and I have not touched the
target.** `CLAUDE.md` is explicit that near-\$1 pair tolerance is later
incentive-era behaviour and stays locked unless equivalent economics are
source-confirmed for our account. This is source evidence about *L5*; it is not
evidence that the same economics are available to us. But it does mean the
current discipline cannot reproduce more than about half his pairs by
construction, and that should be a decision rather than a side effect.

## 3. He is not joining the touch

A first live sample priced Polymarket's two touches at **0.9930**. L5's median
achieved pair cost is **0.9646** — about **3.3 cents better**.

You cannot get 3.3 cents by joining the touch and waiting, which is exactly
what our clone does. The candidates are that he rests deeper and waits, or that
he is selective about the moments he quotes at all. Both are consistent with
needing to be two-sided *early*, because both mean long waits.

n = 1 on the touch side, so this is a hypothesis with one supporting
observation, not a finding. The arm-S instrument now records the distribution
of every priced pair cost, admissible or not, so the comparison will be
properly powered shortly.

## What this changes

1. **The reactive-lateness hypothesis is real but partial.** It fully explains
   the fastest ~10 % and none of the slow tail.
2. **The 0.96 target is now a live, evidenced policy question**, bisecting
   L5's own distribution. Locked until you decide otherwise.
3. **The touch-joining rule is the most likely single cause** of the 3.3-cent
   gap, and it is the one thing we have not yet tested. Testing it means
   resting deeper than the touch — which is *more* patient, not more
   aggressive, so it does not violate any of the constraints on this work.
4. **BTC15m is the cohort to prove this in**, at 96 % two-sided.

## Provenance

- `analysis/l5/pair_formation/l5_trajectory.py` · `tests/test_l5_trajectory.py` (10 tests)
- `analysis/l5/pair_formation/l5_trajectory_report.json` — full output
- source: `analysis/l5/l5_canonical_sequence.csv.gz`, 2,216 windows
