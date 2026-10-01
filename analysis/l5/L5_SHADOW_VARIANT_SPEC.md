# REST/REPLENISH/READJUST diagnostic, and the V1-V4 shadow specification

Checkpoint `6c831d5a`. Frozen benchmark `49799e99` unchanged. Nothing implemented.
Raw: `L5_REST_REPLENISH_DIAGNOSTIC.txt`. Period 2026-09-27..09-30, 384 markets,
33,395 grouped actions.

**Binding evidence limit.** The feed holds FILLS. It holds no orders, cancels,
requotes, resting quotes or queue position. A run of fills at one price is
CONSISTENT WITH a quote resting there; it does not prove one did, since repeated
same-price takes look identical. Everything below is discriminating evidence
BETWEEN mechanisms, never direct observation of resting behaviour.

Two defects in my first run of this diagnostic were found and fixed before
interpretation: the same-price run counter was broken by any fill on the opposite
outcome (so it measured interleaving, not repricing -- now tracked per outcome),
and the "other side held its level" statistic was incremented unconditionally,
making it 1.0000 by construction. Correcting the first moved the headline from
0.9403 to 0.9102, so the conclusion is unchanged; the second became meaningful.

## The verdict: mechanism B, but NOT the "rest and sit" version

| measurement | result | reads on |
|---|---|---|
| per-outcome fills at an UNCHANGED price | **91.02%** of levels produce exactly ONE fill; >=3 fills only 1.80%; max 9 | **against** sit-at-a-level |
| seconds a side holds one level | median **5s**, p90 52s, max 447s | readjust is frequent |
| quantity accumulated at a level | median **20** = one median action | against accumulation at a level |
| one side changes level, other UNCHANGED | **83.83%** of 28,346 events | **for** two independent processes |
| both outcomes fill within 5s / 2s / 1s / 0s | **57.88% / 40.81% / 33.96% / 24.14%** of min(up,down) fills | **for** simultaneous availability |
| return to a PREVIOUSLY USED level | **26.75%** of 29,944 level changes | **for** readjust, against monotone chasing |
| alternation rate (next fill flips outcome) | **36.98%** | **against** sequential A (predicts ~100%) |
| same-outcome run before the opposite fills | median 2, p90 5, max 40 | against A |
| runs occurring while residual == 0 | **60** of 29,944 | he is essentially never flat |

**Mechanism A (OPEN -> deliberately COMPLETE -> reset) is contradicted.** It
predicts near-total alternation; observed is 36.98%. It predicts a flat state
between cycles; residual is zero at 60 of 29,944 level changes.

**Mechanism B is supported in a specific, narrower form.** Not "rest a quote and
let it fill repeatedly" -- 91% of levels yield exactly one fill. Rather:

> TWO INDEPENDENTLY-PRICED ACQUISITION PROCESSES, BOTH CONTINUOUSLY LIVE, EACH
> RE-PRICED AFTER NEARLY EVERY FILL, FREQUENTLY RETURNING TO LEVELS USED BEFORE.

So REST -> FILL -> REPLENISH **AT A RE-CHOSEN LEVEL** -> READJUST. The large
residual remains an EMERGENT consequence of uneven fill rates between two live
processes, consistent with P(EXTEND)=0.5124 being independent of residual.

### A tension this creates with our own earlier finding, flagged not resolved

Our frozen `_still_valid` deliberately HOLDS a resting price rather than chasing
a better touch -- that was the HOLD_OPEN_EXTEND result, and it was real on PMUS.
L5 appears to do the opposite, re-pricing after nearly every fill. Both can be
true (different venue, different queue, and his repricing may be replenishment
after a FILL rather than a cancel-and-chase of an unfilled quote -- which the
data cannot distinguish). V3 vs V4 is designed to separate them rather than
assume either.

## V1-V4, factorized, `49799e99` preserved as CONTROL

`clip=1` throughout. Residual-cap expansion is a SEPARATE later dimension and is
bundled into none of these.

### V1 EXTEND_ONLY
- **Change**: permit acquisition on the heavy side; every other frozen rule intact.
- **Evidence**: 50.56% of L5's actions are blocked solely by `heavy_side_extend_disabled`.
- **Falsified if**: matched contracts/window does not rise materially above
  REPLICATION's 5.0 median on a fresh tape.
- **Risk**: more one-sided acquisition; residual and peak capital rise, both
  already reported per window.

### V2 EXTEND_NO_BASIS_GATE
- **Change**: V1 plus removal of `extend_would_not_improve_basis`.
- **Evidence**: 37.44% of L5's extends worsen his own basis, so the gate cannot
  describe him; the worsening ones are the high-priced fills (median 0.72 vs 0.22).
- **Falsified if**: source-settled economics/window is worse than V1's.
- **Risk**: worse average basis and worse per-contract pair economics. The
  pair-cost floor still binds, so a pair cannot complete above it.

### V3 CONTINUOUS_TWO_SIDED
- **Change**: after a fill, maintain/replenish eligible quotes on BOTH outcomes
  instead of transitioning into complement-first and bank/reset. Conservative
  residual bound retained initially; `clip=1`.
- **Evidence**: 83.83% independent level movement; 57.88% of complement fills
  within 5s; alternation only 36.98%; 60 of 29,944 level changes at residual 0.
- **Falsified if**: V3 does not beat V1 on matched throughput, i.e. the gain is
  attributable to extension alone and not to keeping both processes live.
- **Risk**: two live quotes mean two simultaneous exposures; peak capital and
  one-sided time both rise.

### V4 CONTINUOUS_PLUS_READJUST
- **Change**: V3 plus re-pricing a side after it fills, including returning to a
  previously used level.
- **Evidence is defensible, so this arm is justified**: 91.02% of levels yield
  one fill before the price moves; median level life 5s; 26.75% of level changes
  return to a prior level. No repricing rule is invented beyond "replenish at the
  currently eligible level after a fill" -- which is what the frozen `desired()`
  already computes.
- **Falsified if**: V4 does not beat V3, OR if it degrades queue economics on
  PMUS, where our own HOLD_OPEN_EXTEND evidence says holding a level pays.
- **Risk**: forfeited queue position on every replenish -- directly contrary to a
  finding that replicated on PMUS. This is the variant most likely to fail on our
  venue even if it describes L5 correctly.

## Reporting, every variant

matched contracts/window; matched gross edge/window; source_settled_total/window;
taker fees; residual settlement P&L; `flatten_pnl_shadow` SEPARATELY; peak and
time-weighted residual; capital employed; fills and actions/window; same-side
acquisition rate; both-side acquisition rate; blocked/idle time; final-120s
activity; edge per deployed dollar; worst-window economics.

## Experimental discipline

Batch 1 and Batch 2 are DEVELOPMENT data and will be used only for mechanics and
regression. A fresh untouched tape block will be predeclared before its economics
are inspected -- the remaining archive holds 2026-09-27..09-29 plus 09-30 19Z-21Z,
none of it manifested. 1715z gets no forensic drill-down. No live trading.

## The falsification question this is all built to answer

> Does continuing two-sided acquisition while imbalanced materially increase
> matched throughput and source-settled economics at q1-equivalent size, WITHOUT
> residual/settlement cost growing faster than the additional matched edge?

If not, the REST/REPLENISH reading may describe L5 accurately and still fail to
transfer economically to PMUS. That is a real possible outcome, and the frozen
decomposition already separates the lines needed to detect it.
