# PORTFOLIO_MATCH_V1 — the sizing fix was not the binding constraint

Preregistered in `PREREGISTRATION_PORTFOLIO_MATCH_V1.md` before the
controller was written. 28 untouched windows, $1 normalization, canonical
fees, frozen signal/timestamps/price source/execution. Identity reconciles
to **0.000000** in every window of both arms. Cell C, CF_MATCH, V1 and
D0–D4 unchanged and all re-run identically.

## Result against the gate

| metric | D0 | Cell C | V1-no-seed | CF_MATCH | **PM_V1** | **PM_V1_SEED** |
|---|---|---|---|---|---|---|
| **gross before fees** | +3.8959 | +10.0346 | -27.0638 | -23.5231 | -46.1375 | +111.9621 |
|   matched-pair economics | +0.0000 | +0.0000 | -38.8272 | +62.6577 | +34.0947 | +103.1054 |
|   directional residual P&L | +0.8045 | +1.3342 | +11.7634 | -86.1808 | -80.2322 | +8.8567 |
|   realized directional (exits) | +3.0914 | +8.7004 | +0.0000 | +0.0000 | +0.0000 | +0.0000 |
| **fees** | -4.0741 | -11.4202 | -5.9165 | -12.1671 | -9.5441 | -20.5503 |
| settlement cashflow | 36.804 | 69.334 | 359.608 | 362.940 | 292.887 | 725.331 |
| **net** | -0.1781 | -1.3854 | -32.9804 | -35.6897 | -55.6818 | +91.4118 |
| **net / window** | -0.00636 | -0.04948 | -1.17787 | -1.27463 | -1.98864 | +3.26471 |
| **median window** | -0.01560 | -0.04840 | +0.98680 | -2.65465 | -1.58370 | -3.37960 |
| positive windows / 28 | 13 | 11 | 16 | 5 | 6 | 8 |
| leave-best-1 | -0.01934 | -0.07258 | -1.37635 | -2.07276 | -2.16014 | -2.16893 |
| leave-best-3 | -0.04482 | -0.10993 | -1.78423 | -2.99886 | -2.40326 | -3.49012 |
| worst window | -0.4613 | -0.7785 | -10.9324 | -9.5222 | -8.7508 | -11.9405 |
| peak capital (worst window) | 3.50 | 8.03 | 14.35 | 14.69 | 14.52 | 23.98 |
| **peak residual, shares** | 3.6 | 8.1 | 17.6 | 571.7 | 574.1 | 550.0 |
| matched shares | 0.0 | 0.0 | 103.1 | 317.7 | 281.7 | 510.8 |
| turnover (acquisition cost) | 36.00 | 68.00 | 386.67 | 386.46 | 339.02 | 613.37 |
| gross / fees | +0.956 | +0.879 | -4.574 | -1.933 | -4.834 | +5.448 |
| net per $ of peak capital | -0.00182 | -0.00616 | -0.08208 | -0.08679 | -0.13701 | +0.13612 |
| EXTEND actions | 0 | 0 | 0 | 0 | 204 | 182 |
| MATCH actions | 0 | 0 | 0 | 0 | 183 | 178 |
| MATCHes reaching flat | 0 | 0 | 0 | 0 | 86 | 26 |

| detail | CF_MATCH | PM_V1 | PM_V1_SEED |
|---|---|---|---|
| median pair cost | 0.8694 | 0.8952 | 0.8367 |
| pair cost p10 / p90 | 0.5980 / 0.9959 | 0.6525 / 1.0289 | 0.5831 / 0.9891 |
| %% matched shares below $1.00 | **92.7%** | **79.2%** | **93.7%** |

**PM_V1 fails all four gate criteria** — mean −$1.989, median −$1.584,
leave-best-1 −$2.160, leave-best-3 −$2.403 — and is **worse than the
CF_MATCH it was meant to fix** (−$1.275). No scaling study follows, the
$200 envelope stays unused.

## The preregistered attribution, and it is decisive

### Preregistered attribution

| | |
|---|---|
| matched economics retained vs CF_MATCH's +$62.66 | **54.4%** (+$34.095) |
| residual loss eliminated vs CF_MATCH's −$86.18 | **6.9%** (−$80.232 remains) |
| peak residual | CF_MATCH 571.7 sh -> PM_V1 **574.1 sh** |
| PM_V1_SEED top-1 window share of net | 164% |

The success condition fixed in advance was that residual loss must fall by
**materially more** than matched economics. It fell by **6.9%** while
matched economics fell by **45.6%** — the opposite of the required
direction, and comfortably inside the failure case the preregistration
named.

## I was wrong about the mechanism, and the cap proves it

In `CF_MATCH_RESULT.md` I wrote that the oscillating overshoot "is what
builds a 572-share residual out of 387 one-dollar purchases", and proposed
share-capped matching as the fix that follows from the evidence.

**That diagnosis was wrong.** The cap worked exactly as specified — it bound
on **85 of 183** MATCH actions and drove the book flat **86** times — and
**peak residual did not move: 571.7 → 574.1 shares.**

The reason is visible in the action counts. A MATCH can only *reduce*
residual; it is capped so it can never create any. The residual is therefore
built entirely by the **204 EXTENDs**, which fire whenever the frozen side
equals the residual side *or the book is flat* — including immediately after
a MATCH has just flattened it. The sequence is: toggle → MATCH to flat →
EXTEND the new side repeatedly until the next toggle. Capping the MATCH
removes the overshoot and leaves the accumulation mechanism completely
untouched.

Worse, capping is **economically asymmetric**: it buys fewer of exactly the
shares that were cheap and matched (281.7 MATCH shares against 2,235.7
EXTEND shares), so the share of matched inventory acquired below $1.00 after
fees falls from **83.0% to 64.4%** and the matched book shrinks from 317.7
to 281.7 shares. The fix removed good inventory and left the bad.

## The seeded arm is the same tail artifact, again

PM_V1_SEED shows +$3.265/window — and **164% of that net is its single best
window**, with a median of **−$3.380**, leave-best-3 **−$3.490**, and a
550-share peak residual. This is the fourth time in this programme a
fixed-notional buyer of cheap outcomes has produced a headline mean from one
window (Variant B, CF_MATCH_SEED, V1 and now this). It does not qualify and
I am not treating it as evidence.

## What is now established about the architecture

Three implementations of BUY-BOTH-SIDES → EXTEND have now failed, and the
failures are no longer independent:

| | residual control | result |
|---|---|---|
| V1 | none — hold everything | −$1.178/window, gross negative |
| CF_MATCH | opposite-side buying, fixed dollars | −$1.275/window, 572-share residual |
| PM_V1 | opposite-side buying, share-capped | −$1.989/window, **574-share residual** |
| Cell C | explicit SELL on reversal | −$0.049/window, **8-share residual** |

**Only an explicit SELL has ever controlled the residual in this family.**
Every variant that refuses to sell ends with a residual two orders of
magnitude larger, because repeated same-side EXTENDs accumulate faster than
opposite-side purchases can absorb them — and the opposite side is only
bought when the frozen signal toggles, which it does roughly once per
fourteen purchases.

The matched economics are genuinely good wherever they appear (median pair
cost $0.84–$0.90, 64–84% below par after fees). They are simply not large
enough to pay for the directional residual that the no-SELL architecture
necessarily builds. $62.66 of matched economics against $86.18 of residual
loss was never a sizing problem; it was a **flow-rate** problem between
EXTEND and MATCH, and V1's cap does not touch the flow rate.

## What I am not doing

Not proposing PM_V2 with an EXTEND cap, a residual ceiling, or a
MATCH-priority rule. Each would be a new parameter, and the honest reading
of four consecutive failures is that the constraint is structural: with
~1.2¢/share round-trip costs and a signal that toggles every ~14 purchases,
an architecture that cannot sell cannot hold its residual bounded.

The one configuration in this family that does control residual — Cell C,
with explicit exits — is also the one with positive gross (+$10.03) and the
smallest net loss (−$0.049/window), and its failure is the fee bill, which
the execution lane already closed. That is the whole family's position,
stated plainly.

`historical replay` throughout. No maker, no SELL added, no thresholds, no
scaling, no live orders. Verified compensation $0.0000.
