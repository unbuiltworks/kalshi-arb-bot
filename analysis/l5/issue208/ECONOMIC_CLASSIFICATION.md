# Sequential completion is expected-loss reduction, not expected profit

PR #208, PRIORITY 2. Classification of what the sequential-completion
replay actually demonstrated, and what measurable improvement would be
required before matched-pair execution could be called profitable.

Every figure below is **historical replay** on recorded PMUS BTC15m
ladders. Nothing here is shadow, submitted, filled, settled or realized.

---

## 1. The classification

**Sequential completion reduces an expected loss. It does not demonstrate
positive expected profit, and no cell in the replay does.**

Measured, per first-leg-filled contract, pessimistic queue arm:

| action on a lone filled leg | value/leg |
|---|--:|
| **complete** (best cell, d=2 hold) | **−0.018584** |
| complete (d=0, 120 s or hold) | −0.018931 |
| complete (d=0, 60 s TTL) | −0.019903 |
| hold the leg to settlement | −0.0236 |
| reduce by crossing out | −0.0642 |

Completion is the **best available action** on inventory that already
exists, and it is still a loss. The ordering `complete > hold > reduce`
holds at every depth and every TTL tested.

The reason is adverse selection, measured rather than assumed: a leg fails
to fill precisely when the book moved away from it, so by the time one leg
is held alone the complement is systematically dear. **Every sequential
pair in the replay is dearer than 0.96 — not one below it, in any cell.**
Once a leg has filled alone the position is already a loss in expectation;
completing recovers part of it.

**So the rescue must never be counted as profit.** The entry edge, where
one exists in this data, is the *simultaneous* pair at d=2 — out-of-sample
pessimistic **+0.0163/entry** — not the rescue of a lone leg.

---

## 2. What the measured numbers imply about the pair itself

Let `q` be the completion rate, `g` the gross value of a completed pair
(`1 − pair cost`, as the replay charged it), and `c = −0.0236` the measured
residual carry of an uncompleted leg. Then per first leg:

```
E = q·g + (1 − q)·c
```

The replay reports `E` and `q` directly, so `g` is implied rather than
assumed: `g = (E − (1 − q)·c) / q`.

| cell | q measured | implied gross/pair | implied pair cost |
|---|--:|--:|--:|
| d=0, 60 s TTL | 23.0% | −0.007526 | 1.0075 |
| d=0, 120 s TTL | 29.1% | −0.007555 | 1.0076 |
| d=0, hold | 29.1% | −0.007555 | 1.0076 |
| d=1, hold | 22.6% | −0.001627 | 1.0016 |
| **d=2, hold** | **17.7%** | **+0.004739** | **0.9953** |

This is the sharpest statement the replay supports: **at d=0 and d=1 the
completed pair itself costs more than $1.** Completion at the touch is not
buying a cheap pair; it is buying an expensive one that is still cheaper
than carrying the leg. Only at d=2 — resting two ticks back, where fewer
legs complete at all — is the completed pair gross-positive, and only by
half a tick.

---

## 3. What improvement would be required

`dE/dq = g − c = +0.0160` at the d=0 anchor, so completing more **does**
help monotonically. But `E` caps at `g`:

### A. Completion rate alone — insufficient at d=0 and d=1

At `g = −0.007555`, even a **100%** completion rate gives `E = −0.007555`.
There is no completion rate that makes d=0 profitable. Raising `q` is a
real improvement and the wrong lever to call sufficient.

### B. Completion rate alone — the d=2 cell, and its cost

| cell | q needed at measured gross | with 2 credited maker rebates |
|---|--:|--:|
| d=0, 120 s | impossible (>100%) | 77.2% |
| d=1, hold | impossible (>100%) | 64.6% |
| d=2, hold | **83.3%** | **55.0%** |

d=2 is the only cell that can reach break-even on completion rate alone,
and it needs **83.3%** against a measured **17.7%** — a 4.7× improvement in
the rate of completing a leg quoted two ticks back. The replay gives no
evidence that is attainable; resting further back is *why* its rate is low.

### C. Pair cost — the required improvement, stated as a number

At the measured d=0 rate `q = 0.291`, break-even needs

```
g ≥ −(1 − q)·c / q = +0.0575   i.e. a completed pair at or below 0.9425
```

against an implied **1.0076** today. **The required improvement is
+0.0651 per pair — 6.5 ticks.** That is the single number this program
should be measured against, and it is large.

### D. Rebates — additive, and not close to sufficient

The only rebate this program has **confirmed credited** is
`$2.13 / 293 fills = $0.00727/fill`. Both legs maker-filled gives
`2r = +0.01454` per completed pair:

- d=0 gross goes −0.007555 → **+0.006985** (gross-positive, policy still
  negative);
- the gap to the +0.0575 required at `q = 0.291` closes by **22.4%**;
- the required completion rate falls from impossible to **77.2%**.

Rebates are real and they matter. They are not permission to call this
profitable, and nothing accrued-but-uncredited may be counted at all
(**$1.48 is currently accrued and not credited**).

### E. Residual carry — the wrong lever

With `g < 0`, no reduction in carry makes `E` positive: the carry would
have to become a **gain** of +0.0031/leg. And the measured alternative to
carrying — crossing out at −0.0642 — is 2.7× worse than carrying. There is
nothing to win here.

---

## 4. Where the edge actually has to come from

Stated as the smallest set of measurable conditions, in order of how much
each is worth:

1. **Do not acquire lone legs.** The entry edge measured in this data is
   the simultaneous d=2 pair at **+0.0163/entry**. The whole sequential
   problem is a consequence of one leg filling alone. **The most valuable
   improvement available is not a better rescue; it is a lower rate of
   single-leg fills.** No replay cell competes with simply not being in
   this state.
2. **Pair cost at completion ≤ 0.9425 at the measured 29.1% rate**, or
   **≤ 0.96 at a 37.1% rate**. Either is a 6.5-tick or 8-point improvement
   and must be *measured* on the venue, not modeled.
3. **Both legs maker-rebated and credited**, worth +0.01454/pair — 22.4%
   of the gap, and only once source-confirmed on our own account.
4. **Patience is already exhausted.** Waiting improves completion
   monotonically and **saturates by 120 s** (−0.019903 → −0.018931 → no
   further gain). There is nothing left in longer TTLs in this data.

---

## 5. What this does NOT establish

- It does not establish that matched-pair execution is unprofitable in
  general. It establishes that **on these windows, with these queue
  assumptions, every sequential cell is negative**, and names the size of
  the improvement required.
- It does not establish that the three historical early cancellations were
  mistakes. Those orders faced different inventory and price conditions
  from the simultaneous-entry replay.
- It does not license a live test to "check" profitability. A live window
  measures *fills*, not expectancy, and the gap above is 6.5 ticks wide —
  far more than a bounded probe could resolve.

## 6. Provenance

- `analysis/l5/issue208/sequential_completion_replay.py` — the cells.
- `analysis/l5/issue208/SEQUENTIAL_COMPLETION.md` — the replay's findings.
- `analysis/l5/issue208/QUEUE_AWARE_OOS_REPLAY.md` — the +0.0163/entry
  simultaneous d=2 out-of-sample pessimistic figure.
- residual carry −0.0236/contract and rebate $0.00727/fill: measured and
  credited respectively, carried forward from the settlement audit.
- The `g` column is **derived** from the replay's own `E` and `q`, not
  separately measured; it is exact given those two.
