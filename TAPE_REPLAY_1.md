# Full sequential-tape replay — A vs B, and the COMPLETE price frontier

**The arm expansion is frozen.** The limiting resource is independent market
observations, not parameter combinations. Two policies, one engine, and the
window is the unit.

**The tape is the clock.** Every earlier study stepped cycle by cycle and
asked which prints fell in the interval. That is the wrong clock: a fill
changes inventory, inventory changes what the policy wants, and the next print
an instant later meets a different quote. This engine walks the tape **print by
print** and recomputes inventory and the Model-A action **after every
hypothetical execution, before looking at the next print**. Every window starts
flat.

**Sample: 35 windows offered, 29 sufficiently reconstructed** (≥5 recorded book
cycles), 6 skipped and reported as skipped. **Model A unchanged. Nothing sent.
Every execution is SHADOW/THEORETICAL.**

Code: `tape_replay.py` (30 tests). Reproduce:
`python3 -m src.gemini_execution.tape_replay_report`.
Output: `analysis/gemini/tape_replay.json`.

---

## 1. The two policies, every primary output

| | **A — FROZEN_L5_NATIVE** | **A, no bursts** | **B — GEMINI_RECYCLE** | **B, no bursts** |
|---|---|---|---|---|
| independent windows | 29 | 29 | 29 | 29 |
| windows filled | 16 | 14 | 16 | 14 |
| **windows paired** | **3** | **1** | **3** | **1** |
| initial fills | 16 | 14 | 16 | 14 |
| COMPLETE fills | 3 | 1 | 3 | 1 |
| **EXTEND fills** | **9** | 6 | **0** | **0** |
| EXTENDs vetoed | 0 | 0 | 136 | 123 |
| matched contracts | 0.03 | 0.01 | 0.03 | 0.01 |
| gross contracts | 0.28 | 0.21 | **0.19** | 0.15 |
| **matched / gross** | 0.1071 | 0.0476 | **0.1579** | 0.0667 |
| **terminal residual** | 0.22 | 0.19 | **0.13** | 0.13 |
| **capital at risk** | $0.1323 | $0.0825 | **$0.0859** | $0.0621 |
| settlement residual P&L | −$0.01673 | −$0.00977 | **−$0.01010** | −$0.00720 |
| maker fees | $0.002315 | $0.001444 | $0.001503 | $0.001087 |
| rebate accrued | $0.0010352 | $0.0006605 | $0.0006743 | $0.0004879 |
| **rebate payable after rounding** | **$0.00** | **$0.00** | **$0.00** | **$0.00** |
| fills on an unknown queue | 22 | 19 | 14 | 13 |
| requotes | 588 | 590 | 522 | 526 |

**B dominates A on every axis that matters and costs nothing.** Identical
windows filled, identical windows paired, identical COMPLETE fills — and **41%
less terminal residual, 35% less capital at risk, a less negative residual
settlement, and matched/gross up from 0.107 to 0.158.** The 136 vetoed EXTENDs
bought no matched inventory in A; they were pure exposure.

This is the third independent confirmation of the EXTEND result, and the first
on independent windows with a sequential clock.

## 2. Pair basis — and why the headline is not good news

| policy | n | min | median | max | **at or below par** |
|---|---|---|---|---|---|
| A | 3 | 0.93 | 1.16 | 1.4675 | **1 of 3** |
| A, no bursts | 1 | 1.2733 | 1.2733 | 1.2733 | **0 of 1** |
| B | 3 | 0.93 | 1.16 | 1.49 | **1 of 3** |
| B, no bursts | 1 | 1.49 | 1.49 | 1.49 | **0 of 1** |

**Two of the three pairs formed above par — they are matched losses.** The three
paired windows:

| window | A basis | B basis |
|---|---|---|
| 0430-UP | 1.4675 | 1.49 |
| 0605-UP | 1.16 | 1.16 |
| **1315-UP** | **0.93** | **0.93** |

Exactly **one window in twenty-nine** produced matched inventory at an
economically acceptable basis. B's improvement in matched/gross comes from
shrinking the denominator (gross 0.28 → 0.19), not from making better pairs.

## 3. Without the nine ≥100-contract prints

**The paired result does not survive.** Windows paired 3 → 1, and the one
surviving pair is the *worst* one: basis **1.2733** under A, **1.49** under B —
above par in both. matched/gross falls to 0.048 / 0.067.

8 of A's 28 fills and 4 of B's 19 came from a ≥100-contract print. **The
conclusion is burst-dominated and is reported as such**: on the non-burst tape
alone, neither policy forms an acceptable pair in 29 windows.

## 4. The COMPLETE price frontier — the decisive result

105 missed complement episodes. For each, the minimum improvement over Model
A's own desired COMPLETE that would have taken an external fill, with the queue
re-measured at every candidate price:

| minimum improvement needed | n | share |
|---|---|---|
| 0 ticks (Model A's own price) | 1 | 1.0% |
| 1 tick | 1 | 1.0% |
| 2 ticks | 0 | 0.0% |
| 3–5 ticks | 0 | 0.0% |
| >5 ticks | 4 | 3.8% |
| **crossing required** | **85** | **81.0%** |
| **unreachable at any maker price** | **14** | **13.3%** |

> **81% of missed complements could not have been taken at ANY maker price.**
> The improvement needed reaches the ask, which is a cross, and crossing is not
> authorized. A further 13.3% were not reached by anything within 40 ticks.
> **Only 6 of 105 — 5.7% — were reachable by improving the maker price at all.**

**Price is not the lever.** This closes the question the 4×5 matrix could only
gesture at: the complement does not fail because we quote a tick too low. It
fails because the flow that would fill it never arrives on that side at any
price we are permitted to post.

*(">5 ticks" and "unreachable" were one bucket in the first run, which read as
"expensive" what was actually "impossible". They are separated now and the
separation is pinned by a test.)*

### Completion at the analytical pair ceilings

**These are buckets for reading the table. They are not gates, they are not
proposed, and nothing is wired to them** — the owner ruled against a pair-cost
ceiling in the live path and this does not reopen it.

| ceiling | complements available | completion rate |
|---|---|---|
| ≤0.98 | 3 | 2.86% |
| ≤0.99 | 3 | 2.86% |
| ≤1.00 | 3 | 2.86% |
| ≤1.01 | 3 | 2.86% |
| ≤1.02 | 3 | 2.86% |
| ≤1.05 | 3 | 2.86% |
| unrestricted maker | 6 | 5.71% |

**The table is flat from 0.98 to 1.05.** Of the 6 reachable complements, 3 land
at or below 0.98 and the other 3 land above 1.05 — nothing in between.
Loosening the pair ceiling from 0.98 all the way to 1.05 buys **zero**
additional completions. The only thing that adds any is dropping the ceiling
entirely, and what it adds is three pairs at a basis between 1.05 and 1.47.

Median basis when reachable: **1.11**. Range 0.78 to 1.4675.

## 5. Rebates, kept separate

Computed from Gemini's published formula at the actual hypothetical execution
prices, with the published eligibility band ($0.20–$0.80) and the published
rounding (floored to the cent, at the day).

| | A | B |
|---|---|---|
| accrued | $0.0010352 | $0.0006743 |
| **payable after rounding** | **$0.00** | **$0.00** |
| maker fees | $0.002315 | $0.001503 |

**The rebate pays nothing and maker fees exceed the accrual by 2.2×.** At this
size the incentive line is not merely small, it is negative net of fees. It is
still reported as its own line and never added to trading economics.

## 6. What this establishes about the 39.9% reachable notional

The attribution said 39.9% of notional was reachable at our own price with our
own queue position. **The sequential replay shows what happens when we actually
try to recycle it:**

- We get filled in **16 of 29 windows** — initial fills are not the problem.
- We reach a complement in **3 of 29**, and only **1 of 29** at an acceptable
  basis.
- Improving the maker price converts **5.7%** of the misses, and **81% would
  need a cross**.
- **GEMINI_RECYCLE converts the reachable notional into initial fills just as
  well as the frozen policy and into acceptable matched inventory no better.**
  What it does do is stop paying for the failure: 41% less residual, 35% less
  capital.

**So the answer to the next decision is: not yet, and not by this route.** B is
strictly better than A at containing the damage. Neither converts the reachable
notional into economically acceptable matched inventory at a rate that would
justify anything live, and the one clean pair in 29 windows is a single
observation.

## 7. Caveats that bound every number above

- **29 independent windows, 3 pairs, 1 acceptable.** Everything here is a
  handful of observations. Collection continues.
- **Burst dependence is total** for the paired result (§3).
- **14–22 fills rested on an unknown queue.** Gemini publishes no NO ladder, so
  a NO rung's queue is treated as zero — the optimistic assumption. Those fills
  are counted separately so the result can be read without them; under B, 14 of
  19 fills carry that assumption.
- **Our own size would have changed the book** every one of these executions is
  measured against.
- **6 windows were skipped** for insufficient book. They are reported, not
  silently dropped.
- Settlement direction is recorded and **never used to justify a behaviour**.

---

**Accounting labels.** Everything above is **SHADOW / THEORETICAL**. No order
existed, nothing was submitted, filled, settled or realized. Matched-pair
economics, directional-residual exposure, maker fees and rebate accruals are
separate lines and are never summed. An accrual is not a rebate; a rebate is
not trading P&L. No live authorization is requested and none is implied; Model
A and the TTE bounds are unchanged.
