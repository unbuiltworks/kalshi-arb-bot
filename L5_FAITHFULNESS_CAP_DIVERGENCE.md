# Our 0.96 cap is L5's OUTCOME, applied as our PRECONDITION

Read-only, from committed data: `analysis/l5/inventory_trajectory/trajectory.json`
— **L5's own 527 windows, 62,020 fills, all buys**. Nothing deployed, no order,
no limit changed, `l5-pmus-live` untouched, $90 probe unarmed.

This is a **faithfulness** finding, not an economic one. It is established from
L5's own behaviour and is independent of whether changing anything pays.

---

## 1. The exact figure

A peer session estimated by interpolation that our cap refuses ~64% of L5's pair
formations. The file carries the count directly:

> **`within_0_96_at_formation` = 141 of 506 = 27.9%**

**Our 0.9600 cap refuses 72.1% of the formations L5 actually made.** Worse than
the interpolated estimate, and no interpolation needed.

L5's pair cost at first formation (n = 506): **p10 0.9018, median 0.9900, mean
0.9911, p90 1.0821.** His median is 0.99 and his p90 is *above par*. Our cap sits
below his 10th percentile region and well below his median.

## 2. The trajectory, which is the part neither session had read

| checkpoint | windows reaching | two-sided | matched VWAP pair cost (median) | imbalance_rel (median) | matched qty (median) |
|---|---|---|---|---|---|
| after 2 | 512 | 165 | 0.9900 | **1.0000** | 0.00 |
| after 5 | 501 | 361 | 0.9911 | 0.5000 | 19.20 |
| after 10 | 474 | 447 | **0.9938** | 0.2777 | 50.24 |
| after 20 | 421 | 417 | 0.9886 | 0.1944 | 136.03 |
| after 40 | 349 | 348 | 0.9912 | 0.1431 | 311.19 |
| after 60 | 324 | 324 | 0.9906 | 0.0969 | 477.50 |
| after 80 | 308 | 308 | 0.9861 | 0.0843 | 663.52 |
| after 100 | 290 | 290 | **0.9828** | 0.0761 | 827.48 |

Three things follow, and the module is careful enough to trust on all three — it
explicitly refuses to carry a short window's last row forward, "because that
would manufacture the very convergence this is testing for":

1. **He begins fully one-sided and does not panic.** `imbalance_rel` median is
   **1.0000** at fill 2 — completely unmatched — and decays to 0.0761 by fill
   100. He takes a median of **3 fills** to his first pair (p90 **9**).
2. **He grinds the average down, slowly.** Matched VWAP pair cost declines
   0.9938 → 0.9828 across fills 10 → 100. Modest — about 1.1 cents over 90
   fills — but monotone in trend and on a nested population.
3. **He operates at a scale we do not.** Median matched quantity reaches 827 by
   fill 100, and 290 of 527 windows get there. Our controller runs ~4.5
   fills/sequence.

### The caveat, because the headline number invites an overclaim

`final` reports matched VWAP pair cost median **0.9603** — almost exactly our
cap. It is tempting to read that as "0.96 is L5's destination". **Do not read it
that way from this file.** Unlike the `after_N` checkpoints, `final` takes
`rows[-1]` for every window, so it mixes a 3-fill window's endpoint with a
400-fill window's. It is not on the same axis as the checkpoints. The clean,
nested trend only reaches **0.9828** by fill 100. The direction supports the
reading; this figure does not establish it.

## 3. The divergence, stated structurally

**Our 0.96 is applied as a precondition for entering. L5's comparable number is
an outcome of working inventory across ~100 fills.** We require at the first
trade what he achieves at the hundredth.

That is a divergence of *mechanism*, not of parameter. Raising our cap to 0.99
would make our entry gate behaviourally faithful; it would not make us L5, who
also takes 20× our fill count and converges imbalance over a horizon we never
reach.

It also dissolves a mechanism I proposed and should withdraw.

## 4. Withdrawn: my cap → accumulation → drift chain

I argued the 0.96 cap causes the loss by refusing completion, forcing
accumulation, and pushing windows into the 3+ residual stratum where drift is
−$0.6932/window. **Two objections, both correct, both from the peer session:**

1. **Confound.** A large adverse move causes *both* a large unmatched residual
   *and* a large loss. The stratification controls for nothing, so "3+ windows
   lose more" is equally consistent with the move causing both. My figure is an
   association; the causal chain needs the move held fixed.
2. **My own sweep contradicts it.** If the cap drove drift via accumulation,
   raising the ceiling should have reduced accumulation and improved net. At
   n = 14 it did the opposite at every clip. I cannot hold both the mechanism and
   the reversal.

And L5's data is the control that settles it: **he carries fully one-sided
inventory as a matter of course and is profitable.** So negative drift is not a
property of accumulation as such — it is a property of *our* accumulation, which
is the adverse-selection finding ($0.0051 touch / $0.0074 sweep, Kalshi-measured),
not a cap finding. My drift number is real; it is evidence about **selection**,
not about **carrying**.

Also noted against my own arithmetic: the strata sum to −82.19 over 326 windows
against the −85.2296 over 355 I quoted, leaving 29 windows and $3.04
unaccounted — fractional residuals our 1.56 clips produce. The strata are not
exhaustive and the 3+ figure is a stratum mean, not a decomposition.

## 5. What this does and does not justify

**Established:** we are configured to refuse 72.1% of the behaviour we exist to
replicate, and our gate is applied at the wrong point in the sequence.

**Not established:** that changing it pays. The ceiling sweep reversed on a 40%
larger sample and needs ~812 windows to separate the arms.

These need different justifications and **neither alone justifies a live
change.** A faithfulness divergence is cheap to state and does not require 812
windows to weigh; an economic claim does. I am not proposing a cap change —
`TS.max_pair_cost` is a risk control on a money-capable service another session
owns, and the decision is the owner's.

**What I would put to the owner** is the faithfulness question, not the economic
one: *the controller is configured to decline the majority of what L5 does, and
to require at entry what he reaches after a hundred fills.* That is squarely the
replication mandate in `CLAUDE.md`, and it is answerable without resolving the
cent and a half.
