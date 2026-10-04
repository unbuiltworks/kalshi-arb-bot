# CF_MATCH — replacing Cell C's SELLs with opposite-outcome accumulation

Read-only counterfactual on the frozen Cell C action stream. No threshold,
no maker assumption, no scaling, no live orders. Cell C, V1 and D0–D4
unchanged and all re-run identically. The decomposition identity reconciles
to **0.000000** in every window of every arm.

Construction, entirely from frozen events: Cell C's own 387 purchase
timestamps; the extension side starts as the favorite and **toggles at each
of Cell C's 279 reversal timestamps**; no SELL ever happens; everything is
held to settlement. `CF_MATCH` is the pure arm, `CF_MATCH_SEED` adds the
manual $5+$5 seed.

| metric | Cell C (frozen) | V1-no-seed | **CF_MATCH** | **CF_MATCH_SEED** |
|---|---|---|---|---|
| purchases / exits | 387 / 279 | 387 / 0 | 387 / 0 | 414 / 0 |
| **total gross** | +10.0346 | -27.0638 | -23.5231 | +103.6351 |
|   matched-inventory economics | +0.0000 | -38.8272 | +62.6577 | +129.5722 |
|   directional residual economics | +1.3342 | +11.7634 | -86.1808 | -25.9371 |
|   realized directional (exits) | +8.7004 | +0.0000 | +0.0000 | +0.0000 |
| **total fees** | -11.4202 | -5.9165 | -12.1671 | -21.3830 |
| settlement value | 69.334 | 359.608 | 362.940 | 731.777 |
| **net** | -1.3854 | -32.9804 | -35.6897 | +82.2520 |
| **net / window** | -0.04948 | -1.17787 | -1.27463 | +2.93757 |
| **median window** | -0.04840 | +0.98680 | -2.65465 | -3.52545 |
| positive windows (of 28) | 11 | 16 | 5 | 7 |
| leave-best-1 /window | -0.07258 | -1.37635 | -2.07276 | -2.47576 |
| leave-best-3 /window | -0.10993 | -1.78423 | -2.99886 | -3.82150 |
| worst window | -0.7785 | -10.9324 | -9.5222 | -11.9405 |
| matched shares created | 0.0 | 103.1 | 317.7 | 526.1 |
| peak directional residual (sh) | 8.1 | 17.6 | 571.7 | 557.8 |
| peak capital, worst window | 8.03 | 14.35 | 14.69 | 24.01 |
| turnover (acquisition cost) | 68.00 | 386.67 | 386.46 | 628.14 |
| gross / turnover | +0.1476 | -0.0700 | -0.0609 | +0.1650 |
| **gross / fees** | +0.879 | -4.574 | -1.933 | +4.847 |

### What was eliminated, and what replaced it

## 1. The mechanism is real — this is the part that worked

| variant | windows with matched inventory | median pair cost | p10 | p90 | fraction of matched shares acquired below $1.00 | below $1.00 after fees |
|---|---|---|---|---|---|---|
| Cell C (frozen) | **0 — never holds both sides** | — | — | — | — | — |
| V1-no-seed | 20 | 1.3583 | 1.2223 | 1.6668 | **0.0%** | 0.0% |
| CF_MATCH | 28 | 0.8694 | 0.5980 | 0.9959 | **92.7%** | 83.0% |
| CF_MATCH_SEED | 28 | 0.7532 | 0.5618 | 0.9887 | **97.0%** | 87.3% |

**Buying the opposite outcome after a reversal genuinely acquires the
complement cheap.** CF_MATCH's matched inventory costs a **median $0.8694**
per pair (p10 $0.598), **92.7% of matched shares were acquired below $1.00**
and **83.0% are still below $1.00 after fees**. Matched-inventory economics
are **+$62.66** (CF_MATCH) and **+$129.57** (CF_MATCH_SEED).

Compare V1-no-seed, which also ends up holding both sides but only because
leadership flips force it: its median pair cost is **$1.3583** and **0.0%**
of its matched shares were acquired below par. And Cell C never holds both
sides at all — it flattens, so it creates **zero** matched inventory.

So the hypothesis is confirmed on its own terms: *the opposite side does
become the complement naturally, and it does so at a genuinely good price.*

## 2. The economics still fail, for a reason that is not the fee bill

| | Cell C | CF_MATCH |
|---|---|---|
| matched economics | $0.00 | **+$62.66** |
| directional residual economics | +$1.33 | **−$86.18** |
| peak directional residual | **8.1 sh** | **571.7 sh** |
| median terminal residual | 2.0 sh | 37.1 sh |
| net/window | −$0.049 | **−$1.275** |
| median window | −$0.048 | −$2.655 |
| positive windows | 11/28 | 5/28 |
| leave-best-1 | −$0.073 | −$2.073 |

**Deleting the sells converts a bounded-residual strategy into an
unbounded one.** Cell C's residual never exceeds 8 shares because every
reversal flattens it. CF_MATCH's reaches **572**. The matched book it
builds is worth +$62.66; the residual it cannot avoid building costs
−$86.18. The strategy loses by the difference.

## 3. The counterintuitive part: eliminating the SELLs did not save fees

| | |
|---|---|
| Cell C SELL legs eliminated | 279 exits, $327.37 notional, **$5.5037 of exit fees** |
| purchases redirected to the opposite outcome | **158 of 387 (40.8%)**, $157.60 notional, $8.5511 of fees |
| **net change in the fee bill** | **+$0.7469 — CF_MATCH pays MORE** |

The venue's fee is `θ·p(1−p)` **per share**, so a fixed $1 buying a cheap
side buys many shares: $1 at $0.10 costs 6.3¢ of fee, $1 at $0.50 costs
3.5¢. Redirecting purchases to the weaker (cheaper) side therefore *raises*
the fee bill by more than the 279 eliminated exits saved. The turnover
story is unchanged too — $386.46 against Cell C's $68.00, **5.7× the
turnover for a worse result.**

## 4. CF_MATCH_SEED's +$2.94/window is one window

181% of its total net comes from its single best window (+$149.10). Median
**−$3.53**, leave-best-1 **−$2.48**, leave-best-3 **−$3.82**, 7 of 28
positive, peak residual 558 shares. This is the Variant B pathology
exactly: fixed notional at a low price is an unbounded share count, and the
aggregate is the payoff of a few longshots that landed. It does not
qualify and it is not evidence for the architecture.

## 5. What this actually tells the architecture

The architecture is **not** refuted — one more implementation of it is. And
the failure has a specific, mechanical cause that points at the fix:

> **$1 of the opposite outcome does not match $1 of residual.** Residual is
> measured in *shares*; a fixed-notional purchase at price *p* delivers
> `1/p` shares. After a reversal the opposite side is cheap, so every
> matching purchase overshoots — and then the next reversal makes the
> *other* side cheap and overshoots back. That oscillation is what builds a
> 572-share residual out of 387 one-dollar purchases.

A controller whose state is **(YES shares, NO shares, matched, residual,
average costs, favorite, recent movement, time remaining)** would size the
opposite-side purchase **in shares, against the residual it is matching**,
not in fixed dollars. That is the difference between *accumulating* and
*matching*, and it is the one change this evidence actually supports.

It also sets the economics to beat: at CF_MATCH's observed pair cost,
**matched inventory earns about 13¢ per matched share** (median pair $0.8694
→ $1.00 at settlement), and 83% of it clears $1.00 even after fees. A
controller that captured that while holding the residual near Cell C's 8
shares instead of 572 is the thing worth preregistering.

**I am not preregistering it in this turn** — that is a new controller, and
it needs its own document with the sizing rule stated before any result. On
the gate that matters: CF_MATCH fails mean, median, leave-best-1 and
leave-best-3, so nothing here advances to scaling, and the $200 ceiling
stays unused.

## Accounting note

UP and DOWN shares, cash and average costs are carried separately
throughout. A DOWN purchase is priced at `1 − bid` and consumes UP **bid**
depth; selling UP would *receive* `bid`. **Buying the opposite outcome is
never treated as selling the original**, and the venue's single long-side
wire price never enters the economics. `historical replay`; verified
compensation $0.0000.
