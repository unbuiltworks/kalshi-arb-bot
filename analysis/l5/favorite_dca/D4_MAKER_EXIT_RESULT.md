# D4_TAKER_ENTRY_MAKER_EXIT — the exit is the one place patience is unaffordable

Shadow/replay only, $1 scale, the same 28 untouched windows. D0's controller
and taker entries frozen; only exit execution changed. The lifetime was
derived from risk mechanics and frozen in `PREREGISTRATION_D4.md` **before**
these numbers existed. No entry change, no threshold sweep, no scaling, no
live orders. The decomposition identity reconciles to **0.000000** in all 28
windows.

## Result: the gate fails, and by an order of magnitude

| metric | **D0** (taker exits, frozen control) | **D4** (maker exits) |
|---|---|---|
| gross before fees | +3.8959 | -1.5101 |
| total fees | -4.0741 | -3.7971 |
| maker rebate, MODELLED (excluded from net) | +0.0000 | +0.0756 |
| **net** | -0.1781 | -5.3072 |
| **mean net/window** | -0.00636 | -0.18954 |
| **median net/window** | -0.01560 | -0.14670 |
| positive windows (of 28) | 13 | 8 |
| worst window | -0.4613 | -0.9593 |
| leave-best-1 /window | -0.01934 | -0.20591 |
| leave-best-3 /window | -0.04482 | -0.23666 |
| max drawdown, worst window | 0.61 | 1.07 |
| max residual, worst window (sh) | 3.6 | 3.6 |
| peak capital, worst window | 3.50 | 3.28 |
| capital turnover /window | 3.87 | 3.48 |
| exits | 141 | 141 |

D4 is **30× worse** than D0 on mean net and negative on all four gate
criteria. Gross collapses from **+$3.8959 to −$1.5101** — passive exits do
not merely fail to help, they destroy the gross edge that made this lane
interesting.

## Why — the two sides of the ledger, both measured

**What the passive exit earned, on the exits it won:**

| | |
|---|---|
| exits where a passive offer defensibly filled | **32 of 141 (22.7%)** |
| proceeds on those exits, maker | $37.6700 |
| the same shares at D0's aggressive execution, same trigger book | $36.6357 |
| **improvement (price + avoided taker fee)** | **+$1.0343** |
| modelled maker rebate on them (excluded from net) | $0.0756 |

The execution saving is real and in line with D2's: about 2.7¢ per captured
exit.

**What waiting cost, on the 109 exits it did not win:**

| | |
|---|---|
| fallbacks | **109** |
| adverse movement per share, mean / median / worst | **+3.79¢ / +4.00¢ / +37¢** |
| worse / better / unchanged | **81 / 26 / 2** |
| **total adverse movement charged** | **+$6.0047** |

**$6.00 of adverse movement against $1.03 of execution saving.** Waiting on
an exit costs about **six times** what passive execution saves.

## The asymmetry that explains it, and it is not subtle

Compare the same measurement on the entry side from D3:

| | adverse movement while waiting, per share |
|---|---|
| **D3 entries** (0.713 s wait) | **+0.087¢** |
| **D4 exits** (up to ~59 s, or one trail-width) | **+3.79¢** |

A factor of **44**. An entry signal arrives at an arbitrary moment, so
waiting is close to a coin flip — D3 measured 136 of 184 fallbacks crossing
at an *unchanged* price. **An exit signal arrives precisely because the
price is already moving against us**, so resting an offer into it is
patience at the exact moment patience is most expensive.

The abort statistics say the same thing directly:

| passive exit outcome | count |
|---|---|
| filled | 32 |
| **risk abort — one further trail-width against us** | **65 (46%)** |
| deadline reached unfilled | 44 |

**The frozen risk abort fired on 46% of exits.** The move that triggered the
reversal kept going almost half the time, which is what a trail stop is for,
and the passive exit was standing in front of it.

Note the risk abort did its job: it is the reason D4's worst window is
−$0.9593 rather than worse, and the reason max residual is unchanged at 3.6
shares. The bound worked; what it was protecting was a bad idea.

## Where the lane now stands

Both execution levers have been tested against D0's frozen controller, and
the result is symmetric and conclusive:

| | capture | execution saving | cost of waiting | net vs D0 |
|---|---|---|---|---|
| **D2** maker entries, one full period | 52.3% | +$2.4911 | adverse *selection* −$4.05 of gross | **+$0.162 better** |
| **D3** maker entries, 0.713 s | 3.1% | +$0.1241 | −$0.138 of gross | +$0.096 better |
| **D4** maker exits | 22.7% | +$1.0343 | adverse *movement* −$6.00 | **−$5.129 worse** |

- On **entries**, passive execution is nearly free in movement terms and the
  damage comes through *selection* — the fills you win are the trades you
  did not want.
- On **exits**, there is no selection problem worth the name, but the
  *movement* is brutal, because the exit is triggered by the movement.

D2 remains the best configuration in the lane at −$0.0006/window with a
positive median, and it still fails leave-best-1/-3. D4 is dead: this is not
a timing parameter to adjust, it is a sign problem — the exits that fill
passively are the ones that did not need patience, and the ones that needed
it are precisely the ones that run away.

I am not testing an intermediate exit lifetime. A shorter one converges to
D0 (D3 already demonstrated that collapse on the entry side), a longer one
takes more of a $6.00 charge, and searching between them on 28 windows for a
$0.178 deficit would be the sweep every preregistration in this lane
forbids.

## What is left

The fee line is now fully characterised and cannot be executed away:

- entry fees can be avoided only by accepting adverse selection (D2) or by
  capturing almost nothing (D3);
- exit fees can be avoided only by accepting adverse movement at 6× the
  saving (D4).

What remains untested is **not execution**: it is whether the controller can
generate the same gross edge with **fewer round trips**. D0 pays 195 entry
fees and 141 exit fees to earn $3.90 of gross; the fee bill is a function of
turnover (3.87× per window), and turnover is a property of the trail width,
which is frozen. Changing it is a strategy change, not an execution change,
and would need its own preregistration and its own justification — not a
sweep.

Nothing scaled. $200 ceiling unused. Verified compensation **$0.0000**;
D4's $0.0756 modelled maker rebate sits outside net.
