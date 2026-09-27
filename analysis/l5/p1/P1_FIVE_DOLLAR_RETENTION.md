# P1 — can $5/window be RETAINED? Answer: NO, on the available evidence

**Verdict, stated first because the instruction was to say so explicitly rather
than engineer the answer: no evidence-supported L5 behavior reaches ≥$5/window.**
The binding obstacle is not that our clone gives back the EC edge through bad
choices. It is that **L5's own trading-only economics are +$0.83/window**, and
**83.3% of his $4.96/window came from incentives we do not have.** $5/window is
approximately L5's *entire* per-window result, incentives included.

The frozen n=25 arm (q∈{25,50}, variant B) is **unchanged**. Nothing here selects,
tunes θ, raises q, adds a predictor, imposes a stop, or reads settlement.

## 1. L5's own numbers, per window

`docs/L5_FULL_HISTORY_LEDGER_20260831.md`, 16,501 up/down windows, $7,423,850
turnover:

| | total | per window | share |
|---|---|---|---|
| matched-pair margin | +155,076 | **+9.3980** | |
| directional residual & timing | −141,390 | **−8.5686** | |
| **= net trading cash** | **+13,686** | **+0.8294** | **16.7%** |
| incentive income | +68,208 | **+4.1336** | **83.3%** |
| **= grand total** | **+81,895** | **+4.9630** | |

**Even a perfect behavioral replica of L5, trading at his turnover of $449.90 per
window, earns ~$0.83/window before fees — six times short of $5.** Polymarket
charged him no trading fees; we pay taker fees on every completion. Credited PMUS
liquidity rewards are **$0** and BTC15m is in **no** liquidity program, so his
83.3% is not available to us at any size.

## 2. The corpus that makes this answerable

Built `src/l5_sequence_corpus.py` from `data/l5/raw/activity.jsonl` — 78,000
records, 75,864 TRADEs, of which **67,511 are `btc-updown-15m`: the same market
type and 15-minute horizon as our PMUS windows.** So this is like-for-like, not a
cross-horizon extrapolation. Reconstructed: **527 windows, 66,949 states**, each
carrying running inventory, imbalance, held-side basis, inter-fill delay, TTE and
the action that followed.

Stated limits, because they bound every conclusion above: the feed has **no book**
(so "probability movement" is his own fill-price proxy), **no maker/taker flag**
per fill, and he almost never SELLs (2 of 75,864) — he exits by REDEEM at
settlement, so "carry" means carried to settlement.

## 3. Nearest-state retrieval for our losing states

Five archetypes drawn from the frozen windows' materially losing states, defined
**without settlement information** (pair cost > 1.00 at the moment the second leg
fills; terminal one-sided size). 400 nearest L5 states each, imbalance normalised
to **clips of each trader's own typical fill** so 50 contracts of ours is not
naively compared to 50 of his:

| our state | L5 next: EXTEND | COMPLETE | CARRY | median delay | next size | fills/window |
|---|---|---|---|---|---|---|
| held 1 clip, basis 0.79, TTE 4s | 0.480 | 0.470 | 0.050 | **1s** | 1.00 clip | 198 |
| held 1 clip, basis 0.79, TTE 150s | 0.485 | 0.492 | 0.022 | **2s** | 1.00 clip | 170 |
| held 2 clips, basis 0.40, TTE 120s | 0.547 | 0.412 | 0.033 | **3s** | 1.00 clip | 173 |
| held 2 clips, basis 0.31, TTE 60s | 0.525 | 0.403 | 0.070 | **2s** | 1.00 clip | 169 |
| held 1 clip, basis 0.98, TTE 30s | 0.492 | 0.475 | 0.033 | **2s** | 1.00 clip | 196 |

In every comparable state L5's next action is a **near coin-flip between EXTEND
and COMPLETE**, arriving in a **median 1–3 seconds**, at **one unit of his own
typical size**. He does not choose a side from the state, and he does not wait.

## 4. The documented differences

| dimension | L5 | our PMUS clone | ratio |
|---|---|---|---|
| fills per window | **123** (median) | 3–6 | ~30× |
| delay to next action | **4s** (median gap) | 62–272s (per-window median leg gap) | ~50× |
| fill size | 10.5 contracts, **$3.99** median notional | q=50 ≈ $25 | 6× larger |
| terminal directional carry | **99.1%** of windows, median 116.5 contracts | 4 of 12 windows, 1.75 clips peak | we carry *less* |
| trading fees | **none** | taker fee on every completion | — |
| incentives | **83.3%** of his result | maker rebate only; rewards **$0** | — |

Two of these overturn a natural reading of our own results. **Our residual
exposure is not the deviation from L5 — he carries far more, in 99.1% of windows.**
And our clone is not too aggressive; it is roughly **30× too slow and too large**.

## 5. The challenger, and why it fails

`scripts/p1_challenger_replay.py`. Not a new rule: every difference is an existing
parameter of the frozen scorer, chosen because §4 documents it. Same tape, same 12
frozen windows, same queue-aware fill model, same fee schedule, no settlement in
any decision.

* **variant A** (re-engage immediately) instead of **B** (block same-side rebuild
  for `hold_s`) — because L5's median next action is 1–3s with no cool-off.
* **smaller clip**, down to **q=5**, which is the only rung inside demonstrated
  live capacity (5.31 contracts ever filled) rather than a modeled extrapolation.

Per window, in the required order:

| clip | var | EC gross | passive gross | matched | fees | residual | **TRADING NET** | exp. rebate | **MODELED NET** | peak dir. (contracts / clips) | term. resid | completions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | B | +1.8633 | −1.1112 | +0.7521 | 0.1753 | −0.5838 | **−0.0069** | +0.0867 | **+0.0797** | 8.75 / 1.75 | 4 | 2.83 |
| 5 | **A** | +1.8633 | −1.1112 | +0.7521 | 0.1753 | −0.5921 | **−0.0153** | +0.0867 | **+0.0714** | 8.75 / 1.75 | 4 | 2.83 |
| 10 | B | +3.6955 | −2.2224 | +1.4731 | 0.3474 | −1.1675 | **−0.0418** | +0.1600 | **+0.1182** | 17.50 / 1.75 | 4 | 2.83 |
| 10 | **A** | +3.6955 | −2.2224 | +1.4731 | 0.3474 | −1.1842 | **−0.0585** | +0.1600 | **+0.1015** | 17.50 / 1.75 | 4 | 2.83 |
| 25 | B | +9.3373 | −5.6904 | +3.6469 | 0.8771 | −2.9188 | **−0.1490** | +0.4133 | **+0.2644** | 43.75 / 1.75 | 4 | 3.00 |
| 25 | **A** | +9.0625 | −5.5369 | +3.5257 | 0.8494 | −2.9604 | **−0.2841** | +0.4183 | **+0.1342** | 43.75 / 1.75 | 4 | 2.92 |
| 50 | B | +18.1925 | −11.2533 | +6.9392 | 1.7101 | −5.8375 | **−0.6084** | +0.8217 | **+0.2132** | 87.50 / 1.75 | 4 | 3.00 |
| 50 | **A** | +17.7135 | −10.9997 | +6.7138 | 1.6553 | −5.9208 | **−0.8623** | +0.8308 | **−0.0315** | 87.50 / 1.75 | 4 | 2.92 |

**Variant A is worse than B at every clip.** The L5-documented "no cool-off"
behavior does not transfer: removing the hold slightly *reduces* EC gross and
slightly *worsens* residual. It is recorded as a falsified transfer, not retried.

**Peak directional exposure is 1.75 clips in every single arm** — no arm buys
return with exposure, so the "no greater exposure" condition is met by all of them
and separates none of them.

### The structural finding: everything is a scalar in q

Completions per window are **2.83–3.00 regardless of clip**, and EC scales 9.76×
for a 10× clip. The engine does the *same three completions* whatever the size, so
every line is very nearly a pure multiple of q. **There is no frequency gain
available, because the tape has a median of 52 bbo observations and 43 two-sided
states per window against L5's 123 fills.** We cannot express his frequency from
this data — a resolution ceiling, not a policy choice.

Consequently the only route from +0.21 to +5.00 is size:

| arm | turnover/window | modeled net/window | net per $ turnover | q needed for $5 |
|---|---|---|---|---|
| q=5 | $26.08 | +0.0797 | 0.00306 | **314** |
| q=10 | $51.97 | +0.1182 | 0.00227 | 423 |
| q=25 | $129.49 | +0.2644 | 0.00204 | 473 |
| q=50 | $257.09 | +0.2132 | 0.00083 | 1173 |
| **L5 actual** | **$449.90** | **+4.9630** | **0.01103** | — |

q=314 is **59× the largest clip ever filled live (5.31)**, and reaching $5 that way
is exactly the unsupported linear scaling the instruction excludes. Note also our
best rate, 0.00306 at q=5, **includes an EXPECTED rebate that has not been
credited**; on trading alone every arm is negative, while L5's trading-only rate is
+0.00184. We are not merely smaller than L5 — we are worse per dollar turned over,
before incentives.

## 6. What this does and does not say

**Does not say** the L5 thesis is wrong, or that the behavioral replication was
mis-specified. The response curve reproduces, the state matching works, and our
clone's losing first legs sit inside L5's own documented bands.

**Does say**, with numbers: on PMUS BTC15m, at a clip anywhere near demonstrated
live capacity, **≥$5/window is not reachable from L5's trading behavior**, because
that behavior is worth ~$0.83/window at his turnover and 83.3% of his result came
from an incentive stream this venue does not offer on this market. The EC edge is
real (+1.86 to +18.19/window gross) and the oscillator gives back ~60% of it, but
closing that leak entirely would still leave ~+$3 gross at q=50 against −5.84
residual and 1.71 fees.

**P0 is unaffected and continues unchanged to n=25.**
