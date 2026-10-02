# Pre-exit path study — separation exists at 1 second, and blanket closure cannot use it

2026-10-02. `3e243e4b` frozen. Policy, thresholds, fill rules, recorder and data
unchanged. **No thresholds fitted.** No live orders.

6 complete PMUS windows, q25, ceiling .99: **437 lots — 384 QUALIFYING, 53 FORCED.**
Labels are eventual outcomes; every feature is strictly contemporaneous.

## First, a correction to the feature list as specified

Four of the requested features are **one number** on this venue. The book is
single and YES-denominated, so for a long lot:

    pair_cost        = px + (1 − ex_px)        = 1 − first_leg_move
    dist_from_ceiling = pair_cost − ceiling    = (1 − ceiling) − first_leg_move
    unwind_pnl       = (1 − pair_cost)·take − fee = first_leg_move·take − fee
    complement_move  = − first_leg_move

Verified numerically: at h=1 the QUAL mean `first_leg_move` is −0.0074 and
`best_pair_cost` is 1.0074; `dist_from_ceiling` is +0.0174 = 0.01 − (−0.0074).
**All four report |sigma| = 5.07 because they are the same signal.** Reporting
them as four separating features would quadruple-count one observation.

So there are **two** independent separating signals, not six.

## Separation by horizon (sigma = (FORCED − QUAL) / pooled SE)

| feature | h=1 | h=5 | h=10 | h=30 | h=60 | h=120 |
|---|---|---|---|---|---|---|
| **mark-to-executable move** (= unwind P&L, pair cost, dist-from-ceiling, complement move) | **−3.10** | −3.59 | −3.69 | −3.01 | −2.84 | **−3.96** |
| **time remaining at entry** | −2.02 | −2.35 | −3.05 | −3.86 | −4.18 | **−6.65** |
| spread | +0.38 | −0.28 | −0.13 | +0.29 | −0.46 | −1.94 |
| executable depth | +0.51 | +0.30 | −0.45 | −0.66 | +0.20 | +0.41 |
| net trade flow 30 s | +1.37 | +1.39 | +2.63 | −1.81 | −2.96 | −0.99 |

Levels at h=1 s:

| | QUALIFYING | FORCED |
|---|---|---|
| executable unwind P&L | −$0.2849 | **−$0.5650** |
| best pair cost | 1.0074 | **1.0253** |
| distance above ceiling | +0.0174 | **+0.0353** |
| time remaining (s) | 466.1 | **389.8** |

## Primary question: yes, and at one second

**There is an observable state that materially separates eventual forced losers
from lots that later exit profitably, and it is visible 1 second after the
first-leg fill** — 3.1 sigma on the mark-to-executable move, 2.0 sigma on time
remaining, strengthening to 3.96 and 6.65 by 120 s.

Two things qualify that:

1. **The separation is in degree, not kind.** At h=1 *both* groups sit **above**
   the ceiling (+0.0174 and +0.0353). Neither qualifies yet. FORCED lots are
   simply further underwater — twice as far, consistently.
2. **Time remaining is an entry-time property, not a path property.** FORCED
   lots are opened ~76 s later in the window (390 s vs 466 s remaining) and
   strengthen monotonically to −6.65 sigma. Less runway, less chance the market
   comes back. This is the one signal available *before* any adverse move.

Spread, executable depth and recent flow carry **no reliable separation** —
spread and depth never exceed ±0.8 sigma, and flow flips sign across horizons
(+2.63 at h=10, −2.96 at h=60), so it is noise on 53 forced lots.

## Exploratory counterfactual frontier — diagnostic only, not a selection

Close **every** still-open lot at the evidenced executable price at horizon h:

| h | lots open | losses avoided on FORCED | profits sacrificed on QUAL | net difference | future winners killed | worst lot |
|---|---|---|---|---|---|---|
| 1 | 428 | +192.98 | +192.67 | **+0.30** | **100.0%** | −5.33 |
| 5 | 334 | +175.70 | +172.12 | **+3.58** | **100.0%** | −5.33 |
| 10 | 261 | +166.05 | +150.98 | **+15.06** | **100.0%** | −4.55 |
| 20 | 205 | +143.33 | +162.84 | −19.51 | 100.0% | −5.99 |
| 30 | 179 | +128.94 | +145.56 | −16.61 | 100.0% | −6.61 |
| 60 | 128 | +90.81 | +120.48 | −29.67 | 100.0% | −9.80 |
| 120 | 76 | +73.73 | +41.08 | **+32.65** | 100.0% | −10.76 |

### Why "100% of future winners killed" is the whole story

The mark-to-executable unwind P&L is **negative for both groups at every
horizon** — QUAL averages −$0.28 at h=1 and −$1.40 at h=60. Closing now always
pays the half-spread plus whatever adverse move has already happened. The
eventual winners are winners precisely *because they later recover past that*.
So blanket closure at any horizon converts **every** one of the 384 qualifying
lots from a small gain into a small loss, to spare 53 large losses.

The two effects are the same size: at h=10 it avoids $166.05 and sacrifices
$150.98 — **net +$15.06 against a base of −$136.12**, and the sign is unstable
across horizons (+15 at h=10, −30 at h=60, +33 at h=120). **Blanket early
closure is not a fix.**

### What it does deliver is tail compression

| h | worst lot if closed now | actual worst FORCED | tail cut |
|---|---|---|---|
| 1 | −$5.33 | −$17.82 | **70%** |
| 10 | −$4.55 | −$17.82 | **74%** |
| 30 | −$6.61 | −$17.82 | 63% |
| 120 | −$10.76 | −$17.82 | 40% |

**A real 63–74% reduction in worst-lot tail at essentially unchanged net P&L.**
The loss is not removed, it is redistributed from a concentrated tail into a
diffuse spread cost — which matters for risk sizing even though it does not
create edge.

## What this establishes, and what it does not

**Established:** the forced-loss bucket is *foreseeable* — 3 sigma at one
second, from two independent and genuinely contemporaneous signals. The failure
is not unpredictable adverse selection.

**Not established:** that it is *avoidable*. Acting on the signal by closing
everything costs as much as it saves, because the signal is negative for winners
and losers alike and only differs in magnitude. A rule that acts on magnitude —
closing only the lots furthest underwater, or only those opened late in the
window — is exactly the threshold fit I was told not to perform, and this
53-forced-lot sample could not support one honestly anyway.

The next legitimate step is more complete windows, with any candidate rule
pre-registered before it is scored.
