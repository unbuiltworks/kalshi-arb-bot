# P2 — portfolio-value extend controller: step 1 dataset, and the first controller

Hybrid execution study is closed and untouched. The WS adapter is untouched.
Read-only analysis plus one new controller; no threshold sweep, no live
orders.

## P2 step 1 — the action-level marginal-value dataset

387 decision opportunities (both sides priceable at every one) plus 289 exit legs. Marginal value is exact and additive — `shares x 1{side settles} - cash - fee` — so it needs no lot convention and must tie to the portfolio net.

**Tie-out:** purchases -32.981 + exit legs +31.595 = **-1.3858**, against frozen Cell C's **-1.3854**. Difference 0.0004, per-window rounding.

*(Caught in build: an earlier pass recomputed the chosen action from the book rather than reading the frozen log, and missed the tie-out by $2.31. Everything below reads the log.)*

| | total | per window |
|---|---|---|
| purchases | -32.981 | -1.1779 |
| exit legs | +31.595 | +1.1284 |
| **net** | **-1.386** | **-0.0495** |

### Where value is generated across the 387 purchases

- positive marginal value: **282 (72.9%)**, total **+74.377**
- non-positive: **105 (27.1%)**, total **-107.357**
  - best 51 purchases (13.2% of all) carry 50% of the positive value
  - best 103 purchases (26.6% of all) carry 80% of the positive value
  - best 282 purchases (72.9% of all) carry 100% of the positive value

### How many actions can disappear while keeping the gross (ex-post ranking — DESCRIPTIVE, not a rule)

| keep best N purchases | % of 387 | purchase value | cash | net/window at $1 | at $100 (x12.45) |
|---|---|---|---|---|---|
| 25 | 6% | +20.268 | 25.79 | +0.7239 | +9.014 |
| 50 | 13% | +36.792 | 51.36 | +1.3140 | +16.364 |
| 100 | 26% | +58.857 | 102.45 | +2.1020 | +26.177 |
| 150 | 39% | +69.740 | 152.91 | +2.4907 | +31.018 |
## The single most important line in this dataset

> **Cell C's purchases are worth −$32.98. Its exit legs are worth +$31.60.**

The round trip *is* the strategy. The buying loses money on its own; the
selling is what monetises it. That reframes the whole premise of P2: the
ENTRY/EXIT churn is not overhead wrapped around a profitable core — it is
the core, and the "extend" part is the overhead.

### Where value is generated across the 387 purchases

- positive marginal value: **282 (72.9%)**, total **+74.377**
- non-positive: **105 (27.1%)**, total **-107.357**
  - best 51 purchases (13.2% of all) carry 50% of the positive value
  - best 103 purchases (26.6% of all) carry 80% of the positive value
  - best 282 purchases (72.9% of all) carry 100% of the positive value

### How many actions can disappear while keeping the gross (ex-post ranking — DESCRIPTIVE, not a rule)

| keep best N purchases | % of 387 | purchase value | cash | net/window at $1 | at $100 (x12.45) |
|---|---|---|---|---|---|
| 25 | 6% | +20.268 | 25.79 | +0.7239 | +9.014 |
| 50 | 13% | +36.792 | 51.36 | +1.3140 | +16.364 |
| 100 | 26% | +58.857 | 102.45 | +2.1020 | +26.177 |
| 150 | 39% | +69.740 | 152.91 | +2.4907 | +31.018 |
| 200 | 52% | +73.412 | 203.16 | +2.6219 | +32.651 |
| 282 | 73% | +74.377 | 285.23 | +2.6563 | +33.080 |
| 387 | 100% | -32.981 | 392.59 | -1.1779 | -14.668 |

### The alternative action at the same 387 opportunities

| action | total value | per window |
|---|---|---|
| what Cell C chose (from the log) | -32.981 | -1.1779 |
| same side, recomputed at the book | -30.668 | -1.0953 |
| **always the OTHER side** | **+110.103** | **+3.9322** |
| **always WAIT** | **0.000** | **0.0000** |
| perfect foresight (unattainable upper bound) | +484.059 | +17.2878 |

The recompute sits +2.313 from the log, which is the book-row selection difference and bounds how much weight the counterfactual column can carry.

### Residual mechanics at the decision point

| purchases that... | n | mean value | total |
|---|---|---|---|
| created new matched shares | 80 | +0.01860 | +1.488 |
| REDUCED |residual| | 70 | +0.01368 | +0.957 |
| INCREASED |residual| | 317 | -0.10706 | -33.938 |

### By seconds remaining
### Every "promising" counterfactual is the same one window

"Always the other side" looks like **+$3.93/window**. It is not:

| | |
|---|---|
| total | +110.103 |
| **best window** | **+181.99 — 165% of the total** |
| positive windows | 10 of 28 |
| **leave-best-1** | **−$2.66/window** |
| leave-best-3 | −$5.90/window |

This is the fourth time a cheap-side variant has produced a headline mean
from one window. The ex-post "keep the best N purchases" column above has
the same character and is published as description only — it is not a rule
and cannot be traded.

## The first controller derived from the mechanics

The dataset says 317 of 387 purchases **increase** |residual| and are worth
**−$33.94**, while the 70 that **reduce** it are worth **+$0.96**. The
economically justified controller that follows — no threshold, no fitting —
is exactly the architecture you asked for:

**SEED BOTH ONCE → only ever buy the UNDER-HELD side, capped so it cannot
flip the residual → WAIT whenever flat → settle.** No leadership input at
all, no exits, WAIT is free.

| | Cell C (frozen) | CF_MATCH | **P2_MATCH_ONLY_SEED** |
|---|---|---|---|
| actions | 666 | 387 | **144** |
| gross | **+10.035** | −23.523 | **−5.487** |
| fees | 11.420 | 12.167 | 10.731 |
| **net/window** | **−0.0495** | −1.2746 | **−0.5792** |
| median | −0.0484 | −2.6547 | −0.5642 |
| positive windows | 11/28 | 5/28 | **2/28** |
| LB1 / LB3 | −0.073 / −0.110 | −2.073 / −2.999 | −0.731 / −0.795 |
| peak capital | 8.03 | 14.69 | 23.36 |
| peak residual | **8.1 sh** | 571.7 sh | 32.8 sh |
| turnover | 68.00 | 386.46 | 344.96 |

**The action count falls 666 → 144 exactly as intended, and the gross goes
with it: +10.04 → −5.49.** Removing the churn removed the thing that was
making money. Residual control is excellent (32.8 shares, versus CF_MATCH's
572) and it buys nothing, because the $5+$5 seed pays two half-spreads at
near-par and the matching purchases buy the under-held side at full price
roughly half the time on the losing side.

## The two questions, answered

**Q: How many of Cell C's 676 actions can disappear while retaining its
$4.28/window gross?**

**By this route, none.** Every action-count reduction tested removes gross
faster than it removes cost, because the gross is produced by the
entry→exit round trip rather than by holding inventory. The only
reductions that *look* profitable are ex-post rankings or one-window
artifacts.

**Q: Does the resulting controller have positive net expectancy before any
increase in capital?**

**No.** −$0.579/window, median −$0.564, 2 of 28 windows positive,
leave-best-1 −$0.731. It is worse than frozen Cell C on every measure
except residual control.

## Two operational findings worth more than the controller

**1. The credentials already exist in Railway.** `pmus-ws-evidence-recorder`
carries `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` as service variables —
I read the **names only**, never the values. Railway's reference syntax
(`${{pmus-ws-evidence-recorder.PMUS_API_KEY_ID}}`) can hand them to another
service without anyone re-entering or reading them, so **nothing needs to
be recreated**. What is still needed is a decision to attach them to an
executor service, and separately to this session if signed preflight is to
be run from here.

**2. The post-development windows exist and are persisted.** The recorder
mounts a **50 GB volume at `/state/pmus-ws`** and writes daily tape parts
to it, so every window since 2026-10-02 is on disk, not merely in the log
buffer. It has **no public domain and no TCP proxy**, so the data cannot be
fetched over HTTP today — but `PMUS_WS_TRADES_HTTP` is already a
recognised variable, which means the publish surface exists in the code.
The cheap unlock is a domain plus that flag, or a one-shot read service
mounting the same volume. Until one of those exists the OOS sample is
**unreachable, not absent** — and I did not run an OOS comparison on data
I cannot read.

## What I would not do next

Build P3 by tuning which purchases to skip. Four studies now agree that
every selection rule that looks profitable on these 28 windows is carried
by one or two settlements. The binding constraint is unchanged and is not
a controller-design problem: **28 windows cannot resolve effects of this
size**, and the data that could is sitting on a volume we have not yet
plumbed. Plumbing it is a half-hour of infrastructure and is worth more
than another variant.

`historical replay`. Verified compensation $0.0000.
