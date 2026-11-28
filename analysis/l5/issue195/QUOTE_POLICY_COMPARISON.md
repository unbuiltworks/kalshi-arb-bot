# Quoting-policy comparison on settled net P&L — and why no live test follows

Simulator: `src/l5_quote_policy_sim.py`. Twelve policies over the recorded
book ladders of 0300z (117 frames, in-sample) and 0315z (99 frames, held
out). Fill model, fees and residual settlement as documented in that module.

## The headline: no policy is both positive and sign-stable

| policy | IN 0300z | OOS 0315z | total | sign-stable |
|---|---|---|---|---|
| P11 2 ticks, imbalance cap 2 | +0.730000 | -0.640000 | +0.090000 | no |
| P2 1 tick, ceil 0.96 | -0.290000 | +0.270000 | -0.020000 | no |
| P8 2 ticks, imbalance cap 1 | +0.210000 | -0.350000 | -0.140000 | no |
| P9 cap 1 + completion | +0.210000 | -0.350000 | -0.140000 | no |
| P5 2 ticks, ceil 0.92 | -0.210000 | 0.000000 | -0.210000 | no |
| P1 at touch | -0.230000 | 0.000000 | -0.230000 | no |
| P10 1 tick, cap 1 | -0.490000 | +0.160000 | -0.330000 | no |
| **P0 current: 2 ticks, 0.96** | **+0.730000** | **-1.130000** | **-0.400000** | no |
| P6 current + completion | +0.730000 | -1.130000 | -0.400000 | no |
| P4 2 ticks, ceil 0.94 | -0.970000 | -0.600000 | -1.570000 | YES (negative) |
| P7 0.94 + completion | -0.970000 | -0.600000 | -1.570000 | YES (negative) |
| P3 3 ticks, ceil 0.96 | -0.780000 | -1.090000 | -1.870000 | YES (negative) |

**Every policy with a positive window flips sign out of sample. The only
sign-stable policies are sign-stable NEGATIVE.** The nominal "best" total,
P11 at +0.09, is +0.73 in one window and -0.64 in the other: that is one
window's luck, not an effect. With n = 2 no policy can be selected, and
selecting one would be fitting noise.

## What IS robust across all three windows we have

**1. Pair cost is not the problem when both legs fill.** Realized pair cost
whenever pairs formed: 0.81750, 0.88500, 0.90500, 0.91000, 0.93250, 0.94750,
0.96000 in simulation, and 0.98748 live. All of those leave positive margin.

**2. The loss is always the residual.** Every negative line above is a
residual line, not a matched line. `matched_pnl` is >= 0 in every single
policy and window.

**3. The residual comes from one-sided fill in a trending book.** In 0315z
the bid fell 0.37 -> 0.01 across the window, so the UP leg filled four times
and the DOWN leg never filled at all. Adverse selection is not symmetric
when the book trends: the side that keeps filling is the side that is losing.

**4. Inventory capping reduces the damage but does not reverse it.**
Uncapped the 0315z residual costs -1.13; capped at 1 it costs -0.35, a 69%
reduction. It is still a loss, because one unmatched UP in a collapsing
market is lost whatever the cap.

**5. The $0.25 improvement floor is validated.** In 0315z the completion
policies (P6, P9) returned exactly their uncapped equivalents — the floor
refused to complete a residual at own 0.99, where improvement is 0.01. That
is the correct refusal and it is the floor doing its job, on real data.

## The breakeven that decides the programme

```
margin from a clean 4-pair window at realized pair ~0.95   +0.20
cost of a one-sided window, imbalance capped at 1          -0.35
cost of a one-sided window, uncapped (current policy)      -1.13

good windows needed per bad window just to break even
  capped     1.75 : 1
  uncapped   5.65 : 1

observed in this sample                                     1 : 1
observed live 20:15z   realized pair 0.98748, margin only  +0.0386
```

At the live window's realized pair cost of 0.98748 the margin was 1.25 cents
per pair, so a clean window yields about +0.05 and the required ratio rises
to roughly **7 : 1 capped** and **23 : 1 uncapped**.

**This is the finding.** The question is not how to quote better. It is
whether clean windows outnumber one-sided windows by enough. Nothing in the
data we hold answers that, because we hold three windows.

## Sample limits, stated rather than worked around

* **n = 2 replay windows, both settled DOWN.** No variation in the outcome
  that determines every residual sign.
* **Frames begin ~115 s into each window.** The 93-second roll stall also
  blocked the sampler, so the opening of each window — where the first pair
  would form — is unobserved.
* **The samples are residual-conditioned.** They were recorded while
  unmatched inventory existed, which biases the sample toward exactly the
  legging state being studied.
* Maker rebates counted as zero throughout, because no `venue_actual` has
  ever confirmed one.

## Recommendation: no live validation yet

Proposing a two-window live test on an n=2 result whose ranking inverts out
of sample would be fitting noise with capital, and the scoped operating
model forbids exactly that ("If evidence is negative or missing, label
BLOCKED and state the missing mechanism").

**BLOCKED on: the clean-window to one-sided-window ratio.**

The data to settle it already exists and costs nothing to read. The shadow
ledger on `/data` holds **2,174 finalized scored windows** —
`L5_SHADOW_LEDGER {"finalized":2173,...}` at the 18:00z roll, 270 MB, about
22 days. Each carries matched and residual shares, residual cost and all
three P&L lines per threshold/envelope cell. That is three orders of
magnitude more evidence than this comparison used, it requires no new
capital, no new authorization and no campaign, and it answers the one
question that decides whether the strategy can work.

The next experiment is therefore a READ, not a trade: score every policy in
this module against those 2,174 windows and report the distribution of
clean versus one-sided outcomes with the resulting expectancy. A live
proposal should follow only if some policy is sign-stable across hundreds of
windows.

Note the dependency: that read is also what the ledger-retention fix is
gated on, and the retention fix has its own deadline — `save()` reaches the
10 s roll budget at about 3,223 records, roughly 11 days out. Reading the
ledger for this analysis and backing it up before compaction are the same
piece of work.
