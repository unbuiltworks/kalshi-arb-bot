# Sequential completion: the rescue is damage limitation, not profit

PR #208 **PRIORITY 1**. Harness
`analysis/l5/issue208/sequential_completion_replay.py`, tests
`tests/test_l5_sequential_completion.py` (12). No `src/` change. Production
disarmed at `6116675ed`. Settled realized net unchanged: **−$15.6109**,
**−$13.4809** rebate-adjusted.

**This is a different experiment from `queue_scenarios.py` and its numbers
do not carry across.** That harness rests both legs at once and scores the
pair they were quoted at. Here one leg fills, the book *moves*, and the
complement is priced against the new touch — which is ROUTE B's actual
situation, and the only way a 0.98 pair arises.

**It is also not evidence about the historical cancels.** Those orders faced
their own inventory, prices and runway; this is a different population.

---

## 1. The deployed decision function cannot be replayed on this data

Measured, not guessed. `BC.decide` requires
`stream_age_s <= TS.MAX_BOOK_AGE_S = 2.0`. The median frame gap in **every**
ladder recording in the repository is **6.4–6.9 s** — about three times the
limit. Asked honestly at each frame across the out-of-sample set, over 265
sequential cases it returned:

| reason | count |
|---|--:|
| `PAST_TERMINAL` | 201 |
| `STALE_BOOK` | 55 |
| no runway to quote at all | 9 |
| **an economic refusal** | **0** |

So a "controller" run of this harness measures the recordings' sampling
rate and says **nothing** about pair economics. `PAST_TERMINAL` is the
second half of it: `complete_cutoff_s() = 720` and leg-A fills land late,
with frames running to `t_in` 778–810 s.

**BLOCKED, and what would unblock it:** ladder snapshots at **≤2 s**
covering the early-to-middle part of a window. Nothing in the repository
does. The blocker is kept reproducible by a test rather than left as a
remark here — if the controller arm ever starts refusing for an *economic*
reason, the data improved and that test should be revisited, not deleted.

## 2. So the economics are measured under an explicitly declared gate

Standing in for the rescue branch, and labelled a **model of** the policy
rather than the policy:

> complete iff the pair beats carrying the leg — `1 − pair > −0.0236`,
> i.e. **pair < 1.0236** — asked once a frame until the TTL.

No ceiling, no budget, no claim, no slot. Its rule is pinned by tests either
side of the boundary.

### Out of sample, entry depth 2, zero rebate, pessimistic arm

| completion depth | TTL | completion rate | **policy/leg** | HOLD/leg | REDUCE/leg |
|--:|---|--:|--:|--:|--:|
| 0 | 60 s | 23.0% | −0.019903 | −0.0236 | −0.0642 |
| 0 | 120 s | 29.1% | **−0.018931** | −0.0236 | −0.0642 |
| 0 | hold | 29.1% | **−0.018931** | −0.0236 | −0.0642 |
| 1 | hold | 22.6% | −0.018634 | −0.0236 | −0.0642 |
| 2 | hold | 17.7% | **−0.018584** | −0.0236 | −0.0642 |

In sample, hold, pessimistic: d=0 −0.015690, d=2 −0.013873 — more
favourable than OOS, the direction to expect.

## 3. What this answers

### Does a second-leg order at the touch fill often enough to justify its price?

**It justifies itself against holding, and the depth barely matters.** At
d=0 the gated policy completes 29.1% and returns −0.018931/leg against
−0.0236 for holding. At d=2 it completes only 17.7% but at cheaper pairs,
and lands at −0.018584/leg. **All three depths finish within 0.0004 of each
other** — because the gate only takes a complement that beats carrying, so
a deeper quote buys fewer but better pairs and the two effects cancel. On
this evidence the completion *depth* is close to irrelevant; the **gate** is
what does the work.

That is a direct correction to the inference I drew from the simultaneous
replay, where `d=0` looked like the setting to question. In the sequential
case it is not.

### Does keeping an unmatched completion resting longer improve outcomes?

**Yes, monotonically, and it saturates by two minutes.** d=0 pessimistic
goes −0.019903 (60 s) → −0.018931 (120 s) → −0.018931 (hold). A test pins
both the ordering and the saturation. Beyond 120 s there is nothing left to
gain in this data.

### Complete, reprice, hold or reduce, on the same inventory

Ordered, at every depth and every TTL: **complete > hold > reduce.**
Crossing out costs **−0.0642/leg**, about 2.7× the measured cost of simply
carrying the leg. Reducing exposure is the worst of the available actions
here, not the safe one.

## 4. The finding that matters most

**Every sequential cell is negative.** The best is −0.0105/leg (d=1, base
arm); the pessimistic arm is ≈−0.0186 everywhere. And **every sequential
pair is dearer than 0.96** — not one below it, in any cell.

That is adverse selection, measured rather than assumed: a leg fails to fill
precisely when the book moved away from it, so by the time we hold one leg
alone the complement is systematically expensive. Once a leg has filled on
its own, the position is **already a loss in expectation**, and completing
recovers part of it.

**So the rescue is damage limitation and must not be counted as profit.**
The edge lives at entry — in the simultaneous pair at `d=2`, where the
out-of-sample pessimistic figure was **+0.0163/entry** — not in the
completion. "More completed pairs" is the right objective only when the
pairs come from both legs filling at the prices we chose; pairs assembled
after one leg has filled alone are a cheaper loss, not a gain.

## 5. Limits

- Bounds, not fills: no trade-print series exists, so every fill is
  `PROVEN`/`POSSIBLE` under a stated queue assumption.
- The gate is **mine**, not the controller's. It has the rescue branch's
  economic content and none of its other guards, and the real controller
  would refuse more, not less.
- Entries overlap heavily; per-leg figures are the only meaningful ones and
  even they are correlated.
- Rebate held at zero. At $0.00727/fill it would add ~+0.0145 on a completed
  pair — enough to turn these cells positive, which is exactly why it is not
  included until credited.
- `PAIR_AT_ENTRY` outcomes are excluded from every row above: both legs
  filling at the entry prices is the simultaneous result and is not the
  completion logic's to claim. A test pins that the sequential rows account
  for themselves exactly.
