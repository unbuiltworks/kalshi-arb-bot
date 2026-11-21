# Inventory-aware policy: evidence and go/no-go

**Verdict: NO-GO on partial hedging and on multi-round accumulation. GO on the
full controlled exit, as a drawdown control and not as a profit improvement.
The owner's priority order (partial hedging > multi-round > exit) inverts what
the evidence supports, and the reason is an identity rather than a measurement.**

Everything below reuses existing infrastructure. No new strategy, service,
repository or experimental lane. New file: one,
`analysis/l5/residual_exit/partial_hedge.py`, which imports the frozen
`dwell_validation` harness unchanged and adds a hedge fraction.

## 1-3. L5's inventory trajectory, his alternatives, and whether it pays

**Already reconstructed and pre-registered; not rebuilt.** See
`L5_STRANDED_INVENTORY_BEHAVIOUR.md`: BTC15m, 5,546 windows, 693,916 fills,
**zero sells**, 38,180 residual episodes, chronological TRAIN/OOS.

His per-episode next action: **EXTEND 66.2%**, SWITCH 18.3%,
COMPLETE_PARTIAL 13.9%, NONE_CARRIED 0.9%, COMPLETE_FLAT 0.8%. Episode exits:
FLIPPED 84.3%, CARRIED_TO_SETTLEMENT 14.5%. Exactly **one of 5,546 windows
ended flat**.

Three findings from that work bear directly on this proposal.

**(a) His inventory management predicts his actions and not his money.** The
same features reach ρ=1.00 predicting his next action and between **−1.0 and
+0.5** predicting window net. `overweight_extend_share` inverts perfectly
(ρ=−1.0). Every OOS spread exceeds its TRAIN spread, which is the signature of
noise. So his inventory behaviour is *characterised*, not *validated*.

**(b) Carrying stranded legs is his single largest loss line.** Matched
+$162,256.93, residual **−$144,065.00**, net +$18,191.93 — carrying consumed
**88.8%** of everything his pairs earned. Out of sample even the favored-side
carried residual lost money (−$12,971, n=432).

**(c) His "never sells" is a venue artefact, not a preference.** Polymarket is
two-token; PMUS is single-instrument, where buying the complement *is* selling
the leg — one order, one price, one fee. His exit vocabulary does not
constrain ours, so "L5 never exits" is not an argument against exiting.

And the scale point that governs everything: our residuals (1.17-3.36 shares)
sit at the **5.7th-15.3rd percentile** of his (median 13.71). In our size
bucket his demonstrated behaviour is EXTEND 0.713 / COMPLEMENT 0.027 — add,
essentially never pair — which he can afford on **32.48 fills/window** against
our **4.52**. His small-residual behaviour is the opening phase of a trajectory
we cannot fund or fill.

## 4-6. Partial hedging, replayed out of sample

`partial_hedge.py`, DWELL trigger, hedge fraction f, 555 eligible windows
(388 TRAIN / 167 OOS), chronological 70/30, no lookahead beyond settlement
outcome, PMUS fees and tick, sell at the bid, no rebate on a cross.

Net per unit of stranded inventory, **OOS**:

| f | mean | median | p05 | stdev | frac < −0.30 | opportunity cost |
|---|---|---|---|---|---|---|
| 0.00 hold | −0.06711 | −0.19740 | −0.59632 | 0.371 | 29.9% | 0 |
| 0.25 | −0.06208 | −0.15756 | −0.45235 | 0.289 | 18.6% | 0.0356 |
| 0.50 | −0.05705 | −0.12071 | −0.31753 | 0.211 | 6.0% | 0.0711 |
| 0.75 | −0.05202 | −0.07496 | −0.18951 | 0.146 | 1.8% | 0.1066 |
| 1.00 full | **−0.04699** | **−0.03231** | **−0.13972** | **0.118** | **1.8%** | 0.1422 |

TRAIN has the same shape (hold −0.05271 → full −0.03797; p05 −0.574 → −0.155;
frac < −0.30 29.4% → 1.6%).

**Three things to read off this.**

**Partial hedging is dominated, not traded off.** Mean, median, p05, standard
deviation and the −0.30 tail *all* improve monotonically as f rises. There is
no risk/return frontier to sit on: f=1 is better than every interior f on both
return and risk. The expected intuition — that a partial hedge buys downside
protection at some cost in mean — is wrong here, because full exit happens to
be better on both.

**No fraction is more supported than any other, as an identity.** Per-unit net
is `f*(bid − cost − fee) + (1−f)*(settle − cost)`, which is **linear in f**.
The harness confirms it to five decimals (observed == predicted_linear at every
f). Because the paired difference vector at fraction f is exactly f times the
vector at f=1, the paired **t is identical (0.732 OOS) for every fraction**.
Choosing f is choosing a scale on one signal, never a different hypothesis — so
no amount of data can make an interior f the answer.

**The mean stays negative at every f.** Even full exit loses −0.047/unit OOS,
and no fraction's advantage over holding is significant at 95% (OOS t=0.732,
ci95 [−0.034, +0.074] at f=1). By the time a leg is stranded with the
profitable completion out of reach, the expected outcome is a loss whatever we
do; exiting fully loses least and loses it most predictably.

That last point is the one with teeth: **inventory management is loss
mitigation, not a profit source.** The profit decision is upstream, at entry
and completion.

### The one mechanism that could rescue partial hedging

This harness **assumes the full hedged size fills at the bid**. A smaller clip
may fill where a full one would not, and that effect is not linear in f — it is
the only way partial could beat full. It is not modelled here and no claim is
made about it. It is testable on our own venue, because `two_sided_queue`
already records `queue_ahead`, `level_depth` and `same_rung_fifo` per order.
Until that is measured, partial hedging has no support.

## 7. Limits and controls

Nothing live was changed. No new live orders, capital, concurrency or
autonomous policy. The existing fail-closed controls, the 0.96 ceiling,
maker-only execution and both loss halts are untouched. This pass is read-only
analysis over frozen CSV corpora.

## 8. Smallest improvement supported out of sample, and the bounded validation

**Wire the already-built dwell exit into the live executor**, full size, as a
drawdown control with its benefit stated honestly:

* it is the only candidate with held-out support on our own venue's economics;
* it targets the line that cost L5 88.8% of his matched earnings and cost our
  current campaign its only loss (0115z, −$0.2304 on a 0.96-share DOWN
  residual);
* it compresses the loss tail hard — OOS p05 −0.596 → −0.140, frac below
  −0.30 29.9% → 1.8%;
* its **mean advantage is NOT significant out of sample** (t=0.732), so it must
  be justified as variance reduction, not as expected profit. Claiming
  otherwise would be overselling 167 windows.
* it costs real upside: **88.4% of OOS exits** were cases where the profitable
  maker completion became reachable later in that same window, and mean
  opportunity cost is 0.142/unit. That is the price of the tail compression and
  it should be reported every time it fires.

It needs only a persisted per-market stranding timestamp in `InventoryStore`,
fail-closed to `None` (falling back to the arm window), passed at the two
`DEF.decide` call sites — the mechanism is already built and inert in
`l5_defensive_completion`.

**Bounded live validation, for separate authorization:** the same capital and
risk limits as the current campaign, exit enabled, and the acceptance measure
stated in advance as *residual loss per eligible window and frequency of
windows worse than −0.30/unit*, not net profit — because net profit is not what
the evidence says it improves.

## What NOT to do

* **No partial hedging.** Dominated by full exit on both mean and tail, and
  dominated as an identity rather than as a measurement.
* **No multi-round accumulation at our size.** `overweight_extend_share`
  inverts out of sample (ρ=−1.0), our whole size range sits inside his smallest
  bucket where his own behaviour is "add and never pair", and we get 4.52
  fills/window against his 32.48. Extending a residual we cannot pair is what
  produced our 0300z −$0.3580 and his −$144,065.
* **Do not raise the 0.96 ceiling** to reach his 0.99 entries. His entry
  economics only work through accumulation we can neither fund nor fill; only
  27.9% of his own entries clear our ceiling.
* **Do not treat any of his inventory management as outcome-validated.** On his
  own data, out of sample, it is not — and his per-fill edge fell **45×** inside
  the corpus (98.0% of BTC15m net earned before the holdout).
