# IS THE BOOK ACTIVE ENOUGH FOR $5/WINDOW WITHOUT LARGE ORDERS?

**Short answer: capacity is not the constraint and the per-fill edge is large.
The constraint is that half of everything we acquire never pairs.** At q30 the
pairs we already form are worth **+$5.39/window gross** — the target is met on
the gross line, and a **−$6.72/window residual drag** destroys it.

Cohort: the consumed 49 (diagnostic only). No fill is inferred from displayed
depth or from a touch: every fill requires queue-ahead exhaustion **and** a print
at or through our own price in the same interval. Artefact:
`analysis/l5/v1_oscillation.json`; code `scripts/l5_v1_oscillation.py`.

## What the owner recording establishes, and it changes the picture

Frames 3 seconds apart: best ask **35¢ → 33¢**, best bid 33¢ → 32¢, with
**1,180–3,243 contracts resting at every single cent**. A 2¢ move in 3 seconds
on a book that deep.

Set against our own data:

| | value |
|---|---|
| traded volume per window (tape) | **median 126,279 contracts** |
| our q10 clip as a share of that | **0.0079%** |
| our observation cadence | 50 rows/window, **bimodal**: 50.6% of gaps ≤5s, 48.9% >15s |
| window time inside a >15s blind gap | **median 730s of 900s (81%)** |
| window time at usable resolution | **median 56s** |

**Capacity is not a constraint by four orders of magnitude.** The market trades
126,279 contracts while we try to buy 10.

The "fast" half of our cadence is not time coverage — it is **burst pairs**: two
samples 2–3s apart, then blind for ~28s. Measured inside those bursts the price
crosses the mid **2 times in 1,114 intervals**; measured across the 28s gaps it
crosses in **457 of 1,051** (43%). The bursts are near-duplicate observations,
not samples of the price path. So a floor of roughly **9 mid-crossings per
window** is visible in the gaps' endpoints alone, and every crossing that begins
and ends a gap on the same side is invisible however many times it really
happened.

## The arithmetic you asked for

Persistent maker, inventory-derived prices, strict queue and print accounting:

| clip | pairs/win | × net edge/pair | = gross | residual drag | **net** |
|---|---|---|---|---|---|
| q10 | 0.98 | $1.8340 | +$1.80 | −$2.24 | **−$0.44** |
| q20 | 0.98 | $3.6679 | +$3.59 | −$4.48 | **−$0.89** |
| q30 | 0.98 | $5.5019 | **+$5.39** | −$6.72 | **−$1.33** |

**Net edge per matched contract: $0.18340** — pairs form at an average cost of
**$0.8166**, nowhere near the $1 ceiling. The edge per fill is not marginal; it
is large.

**Pairs per window: 0.98, at every clip.** Clip does not change how often we
pair, only how much each pair is worth. That is the signature of an
**opportunity-limited**, not capacity-limited, process.

### Two ways to $5, both quantified

| route | q10 | q20 | q30 |
|---|---|---|---|
| more pairs, residual unchanged | 3.95/win (**4.0×**) | 2.58/win (2.6×) | 2.13/win (**2.2×**) |
| same pairs, less residual drag | not reachable | cut 69% | **cut 94%** |

At q30 neither route is absurd: **2.13 pairs per window against a floor of ~9
observable crossings**, or a residual cut that the Variant C work already showed
is mechanically possible.

## The real binding constraint

| clip | acquired/win | matched | **stranded** |
|---|---|---|---|
| q10 | 1,950 | 960 (49%) | **990 (51%)** |
| q20 | 3,900 | 1,920 (49%) | **1,980 (51%)** |
| q30 | 5,850 | 2,880 (49%) | **2,970 (51%)** |

**Exactly half of every contract we acquire never finds its pair**, identically
at every clip. We are filled on the side the market is leaving and not on the
side it is going to — textbook adverse selection against a resting two-sided
quote — and each unpaired contract loses roughly its full basis at settlement.

This is the same finding the residual diagnosis reached from the other end, now
with its magnitude priced: it is not a tail problem, it is **half the book**.

## Answer to the question as posed

> Does this very active book contain enough repeated, realistically fillable
> price oscillation to generate ≥$5/window without requiring huge individual
> orders?

**On this evidence: plausibly yes, and that is a change from my previous
answer** — but it turns entirely on pairing, not on size, speed or capacity.

- **Size is not required.** At q30 the gross line already exceeds $5 with fewer
  than one pair per window, using 0.02% of traded volume.
- **Capacity is not the limit.** 126,279 contracts trade while we seek 10–30.
- **The per-fill edge is large** ($0.1834/contract), so few fills are needed.
- **The limit is the unpaired half.** Every route to $5 runs through pairing more
  of what we acquire, or acquiring less of what we cannot pair.

### What I am NOT claiming

1. The 0.98 pairs/window is what our *observation path* can see, not what the
   book offers. The video shows oscillation at ~3s; we are blind for 81% of the
   window. The gap between 0.98 observed pairs and ~9 observable crossings is
   real but its recoverable portion is unmeasured.
2. The $0.1834/contract edge is conditional on this fill model. It requires both
   legs to fill cheaply, which is precisely the oscillation being claimed —
   circular if the fill model is generous. A reviewer is attacking it.
3. Nothing here is validation. It is a measurement on a consumed cohort.

## What this changes about V1 priority

The ranking from the residual diagnosis stands but its **emphasis moves**:
persistent maker placement matters because it is how the second leg gets caught,
not merely how a stranded residual gets rescued. The first-order V1 question is
now:

> **Of everything we acquire, what fraction can we pair — and what would it take
> to move that from 49% toward L5's completion behaviour?**

That is measurable on forward windows, it is falsifiable, and unlike $5/window it
names the mechanism rather than the target.

I also withdraw, on this evidence, the framing that this architecture is capacity-
or capital-bound at small clip. **It is pairing-bound**, and pairing is a
behaviour, not a resource.
