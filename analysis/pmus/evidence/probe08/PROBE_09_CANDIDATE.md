# Probe 09 candidate — prospective classification. Offline. Not proposed.

The rule: classify on **realized residual + signed outstanding, every live
order counted at full remaining quantity**. `risk_exposure_usd` already
prices an outstanding order as if it fills in full; this makes the inventory
class agree with the risk bound instead of lagging it. `L5_NATIVE` is
untouched — it still chooses side, price, size and timing. This remains a
`SAFETY_OVERLAY` refusal.

Nothing is wired live. Tests assert, on the parse tree, that the module
imports no transport and that no live module imports it.

## Does it close the Probe 08 gap? Yes.

| seq | side | px | realized | outstanding | prospective | class | verdict |
|---|---|---:|---:|---:|---:|---|---|
| 1 | DOWN | 0.41 | 0 | 0 | 0 | FLAT_OPEN | placed |
| 2 | DOWN | 0.47 | 0 | −1.68 | **−1.68** | **INCREASE_RESIDUAL** | **SUPPRESSED** |

Probe 08 classified seq 2 `FLAT_OPEN` because the first DOWN had not yet
**filled**. The candidate suppresses it. That is the defect it was built for,
and on the real numbers it works.

## Does it reduce terminal residual without destroying matched economics? No, on both counts.

| seq | side | px | prospective | class | verdict | real outcome |
|---|---|---:|---:|---|---|---|
| 3 | UP | 0.56 | −1.68 | REDUCE_RESIDUAL | placed | never filled |
| 4 | UP | 0.60 | −1.26 | REDUCE_RESIDUAL | placed | never filled |
| 5 | UP | 0.61 | +0.99 | INCREASE_RESIDUAL | SUPPRESSED | never filled |
| 6 | UP | 0.75 | +0.99 | INCREASE_RESIDUAL | **SUPPRESSED** | **FILLED** |
| 7 | UP | 0.76 | +0.99 | INCREASE_RESIDUAL | **SUPPRESSED** | **FILLED** |
| 8 | UP | 0.80 | +0.99 | INCREASE_RESIDUAL | **SUPPRESSED** | **FILLED** |

| | control (as run) | candidate | delta |
|---|---:|---:|---:|
| orders placed | 8 | 3 | −5 |
| repair orders placed / filled | 6 / 3 | 2 / 0 | |
| matched qty | 3.93 | **0** | −3.93 |
| matched P&L | −0.7806 | 0 | +0.7806 |
| **terminal residual** | 0.79 UP | **1.68 DOWN** | **+0.89** |
| residual P&L | +0.1808 | −0.6888 | −0.8696 |
| maker rebates | +0.0400 | +0.0100 | −0.0300 |
| **total** | **−0.5598** | **−0.6788** | **−0.1190** |

**An unfilled order consumes repair capacity.** Two repairs resting at 0.56
and 0.60 reserve +2.67 of direction, which pushes the prospective residual
to +0.99 and makes every later repair look like deepening a long. The three
repairs it blocks are exactly the three that filled. Terminal residual rises,
matched production goes to zero, and the window ends worse.

The candidate fixes the diagnosed defect and loses more than the defect cost.
On this evidence it is not ready to propose.

## The held-out corpus cannot discriminate — measured, not assumed

Sep 24 + Sep 25: **12,515 decisions, 100.0000% agreement between the two
bases, 0 decisions with a non-empty outstanding book.** Every L5 activity row
is a completed fill, so there is no resting interval for the rule to differ
over. The two bases are the same function on that data.

The rule can only be told apart where orders **rest**, and Probe 08's single
window is the only such evidence held.

## Repair-side adverse selection, quantified

Timebase is L5's own action timestamps — a **lower bound** on rest duration,
because the recovery's order table does not carry the venue's
`insertTime`/`lastTransactTime`. Price movement is L5's successive repair-side
prices, not a book mid.

| seq | offer | qty | t−t0 | outcome |
|---|---:|---:|---:|---|
| 3 | 0.56 | 0.42 | 0s | never filled |
| 4 | 0.60 | 2.25 | 102s | never filled |
| 5 | 0.61 | 2.25 | 102s | never filled |
| 6 | 0.75 | 2.25 | 198s | FILLED |
| 7 | 0.76 | 2.25 | 198s | FILLED |
| 8 | 0.80 | 0.22 | 435s | FILLED |

* first repair offered **0.56**; first repair filled **0.75**, 198s later — **+0.19**
* **every unfilled offer is cheaper than every filled one**
* repair-side price moved **+0.24** over 435s while our orders rested

Pair cost created by each repair fill (FIFO):

    seq 6   1.68 UP@0.75 x DOWN@0.41   pair 1.16
    seq 6   0.57 UP@0.75 x DOWN@0.47   pair 1.22
    seq 7   1.68 UP@0.76 x DOWN@0.47   pair 1.23
    seq 7   0.57 UP@0.76   unmatched -> residual
    seq 8   0.22 UP@0.80   unmatched -> residual
    weighted average                   1.1986

Had the offered repairs filled at their offered prices they would have paired
at **0.97 / 1.01 / 1.07 / 1.08** — counterfactual pricing only, not a claim
they would have filled. The first pair cost **1.16** where the first offer
would have made **0.97**: **0.19 a share** of adverse selection.

No pair-cost ceiling and no price-chasing rule is proposed or implied. This
section is measurement.

## Status

Probe 08 is frozen. No rerun, no capital increase, no live orders. The
candidate is offline and, on the only evidence that can test it, worse than
what it replaces.
