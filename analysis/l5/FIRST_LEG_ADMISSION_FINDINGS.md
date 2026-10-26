# Testing the entry-selection thesis

**Thesis.** L5's advantage may come from selecting first-leg entries by the
future probability and economics of complementary fills, while managing
inventory continuously.

**Verdict: the selection half is NOT supported. The continuous-management
half is, and it is the whole effect.** No decision-time admission rule
improved realized dollars; every gate tested lost money on held-out windows.
What predicts pairing is how much activity follows the opening -- a policy
variable, not a feature.

Scripts: `analysis/l5/first_leg_admission.py`,
`analysis/l5/admission_rule_eval.py`. Nothing deployed.

## Method

`l5_all_trades.csv` has L5's fills but no book, so L5's own fills are the
executable price series: a fill at p proves p traded. This is an **upper
bound** on what we could have captured, because our maker order would have
queued behind L5's.

A first-leg decision is any fill that increases |imbalance| at or before the
T+600s entry cutoff: **21,709** of them across 527 windows. Outcomes are
recoverable for **103 windows** where fills inside 60s of expiry show the
volume-mean implied Up price collapsed to >=0.90 or <=0.10. Those windows
carry REALIZED settlement; the rest are EV-only and were not mixed in.
Split 60/40 by window: 61 train, 42 held out.

## Correction: stranded legs are NOT EV-neutral

| | n | cost | realized | return |
|---|---:|---:|---:|---:|
| unpaired first legs (train) | 378 | $3,976.48 | **-$1,859.30** | **-46.8%** |

I previously concluded strands were EV-neutral and that rescue therefore only
reduced variance. **That was wrong.** At n=6 the signal was invisible; at
n=378 it is unambiguous, and it matches our own six strands at -55.5% of
cost. Conditioning on "did not pair" selects the losing branch -- a resting
maker leg fails to pair precisely when the market leaves it behind. The
earlier rescue conclusion still stands on its own evidence, but its stated
reason was wrong.

## Every admission gate lost money (held-out, realized, incl. strands)

| rule | $/window | pairs | strands | strand $ | pair $ |
|------|---------:|------:|--------:|---------:|-------:|
| **admit everything** | **124.875** | 1731 | 439 | -2711.2 | 7955.9 |
| current (own >= 0.20) | 97.356 | 1347 | 369 | -2585.9 | 6674.9 |
| own >= 0.30 | 84.179 | 1152 | 274 | -2243.9 | 5779.4 |
| own >= 0.20 & t <= 360 | 64.136 | 849 | 199 | -1576.8 | 4270.5 |
| own >= 0.30 & t <= 360 | 54.085 | 758 | 175 | -1456.6 | 3728.2 |
| own >= 0.30 & t<=360 & ratio <= 1 | 53.976 | 641 | 121 | -1038.2 | 3305.2 |
| own >= 0.50 & t <= 360 | 39.515 | 543 | 117 | -912.2 | 2571.8 |
| own >= 0.30 & t <= 240 | 27.154 | 493 | 130 | -1276.0 | 2416.5 |

Pairs out-earn strands roughly 3:1, so restricting entries destroys more pair
profit than strand loss it avoids. The inventory ratio -- the "inventory
aware" feature the thesis names -- was the weakest of the three: pair rate by
|imbalance|/matched is essentially flat (74.6%, 73.5%, 75.0%, 79.2%, 79.0%,
72.4%). **Decision-time inventory state does not predict pairing.**

The apparent price and time signals (pair rate 46.8% -> 87.4% across own
price; 82.7% -> 61.3% across elapsed) are largely confounded by continuation:
late and cheap openings simply have less activity after them.

## What actually predicts pairing

| fills after the opening | n | paired |
|---|---:|---:|
| 0-1 | 70 | **0.0%** |
| 1-3 | 151 | **15.9%** <- our engine's regime |
| 3-10 | 720 | 47.1% |
| 10-30 | 2,263 | 59.2% |
| 30-100 | 9,625 | 75.8% |
| 100+ | 8,880 | **84.6%** |

A monotone dose-response from 0% to 84.6%. This is not a feature to select
on -- it is the policy variable. L5's 75.9% pair rate exists **because** L5
keeps trading, and our 15.9%-regime strands exist because we stop.

## The one admission rule that IS supported

Not price, not time, not inventory ratio: **capacity**. A leg opened with no
remaining ability to continue pairs 0% of the time and then loses 46.8% of
its cost.

> Do not open a first leg unless the policy still has the round budget and
> window time to generate the continuation needed to pair it.

This is the exact mirror of the `completion_capacity_refusal` that already
guards submissions ("a new exposure-increasing order is refused unless the
capacity to close it is still reserved"), applied to rounds instead of
tickets. 2130z is the case it would have caught: one opening, 2.09 contracts
at own 0.52, effectively no continuation, **-$1.0868** -- the campaign's
largest single loss.

## Expected realized net dollars per window

Per-unit economics measured from our own fills: **$0.095** net per completed
pair at our 1.9-contract clip, **-$0.267** per strand (1.9 x mean own 0.30 x
46.8%). With R sequential rounds, earlier openings get paired by later
activity and only the last unfinished round is left, so pairs ~ R-1 and
strands ~ 1:

| R | pairs | net $/window |
|--:|------:|-------------:|
| 1 | 0 | -0.267 |
| 2 | 1 | **-0.172** |
| 3 | 2 | -0.077 |
| **4** | 3 | **+0.018** break-even |
| 5 | 4 | **+0.113** |
| 10 | 9 | +0.588 |
| 15 | 14 | +1.063 |

Validated against our own windows: R=2 predicts -0.172 against observed
**-0.1506** (2100z) and **-0.1766** (2115z). 2130z was R=1 at a 2.09-contract
clip and own 0.52, so its strand is 2.7x the model's mean and it came in at
-1.0868.

**Break-even is R >= 4 rounds per window.** We ran 2.

## Path to the $5 target

| clip | pair $ | strand $ | rounds for $5 | cash needed |
|-----:|-------:|---------:|--------------:|------------:|
| 1.9 | 0.095 | 0.267 | 56.4 | **$107** |
| 6 | 0.300 | 0.842 | 20.5 | **$123** |
| 10 | 0.500 | 1.404 | 13.8 | **$138** |

The cash requirement is nearly **invariant at $107-138**, because pair profit
and strand loss both scale with clip. Cash is `R x clip x $1/share` since
matching does not release capital -- only settlement does. L5's own regime
(10.5 contracts, ~61 pairs/window) sits exactly here.

So $5/window needs roughly **$110-140 of deployed cash**, against the current
$6.00 envelope and $20 hard ceiling. That is a capital decision, and it
should follow a demonstration at R=5 inside the existing ceiling, not precede
it.

## Proposed bounded live validation -- for authorization, not deployed

**Milestone: repeatable positive total net P&L including stranded
inventory.** Not $5 yet.

Change exactly two variables, both inside existing hard ceilings:

    L5_MAX_CASH_DEPLOYED_USD   6.00 -> 15.00   (per-round obligation measured
                                                at $2.99, so 5 rounds)
    L5_TS_ROUND_ALLOCATIONS       2 -> 5

Unchanged: `MAX_TOTAL_EXPOSURE_USD` 20 (hard code ceiling),
`OPENING_LEG_MAX_CONTRACTS` 3, the 0.96 cap, the 0.20 floor, maker-only, one
outstanding order plus the pair slot, daily $5 and cumulative $20 loss
halts, the completion reserve, exact-order cancellation, all cutoffs and
runway gates. No new service, no taker, no clip increase.

Plus the two prepared code changes, already tested and not deployed: the
first-leg floor keyed on the imbalance (`cb22bb47`) and the decision-role
reporting field (`c16ee7ec`).

**Prediction to test:** net moves from the observed -$0.17/window toward
**+$0.11/window** at R=5, with pair count rising from 1 to about 4 per window
and the strand count staying at about 1. 8 windows. If the strand count
scales with R instead of staying near 1, the model is wrong and the
experiment should stop -- that is the single assumption most worth falsifying.

## Honest limits

- Complement executability is taken from L5's own later fills, an upper
  bound; our order would queue behind L5's, and
  `our_position_within_level` is UNKNOWN on every order we have placed.
- Outcomes are recovered for 103 of 527 windows by a price-collapse rule,
  not read from a settlement feed.
- "Pairs ~ R-1, strands ~ 1" is validated on **two** of our windows. It is
  the load-bearing assumption and the explicit falsification target above.
- The admission-rule table is L5's fills at L5's clip, so its absolute
  dollars are L5's economics, not ours; only the ordering of the rules
  transfers.
