# PRE-REGISTRATION — candidate exit rules, fixed before any new window is seen

Written and committed **before** collecting or examining any window beyond the
six discovery windows. `eb706bcc` frozen. Baseline PAIR_OR_UNWIND is unchanged
and is run on every new window alongside every candidate.

## Discovery sample — frozen, excluded from all future scoring

`analysis/l5/pmus_rewards/discovery/DISCOVERY_SAMPLE_tape.jsonl.gz`

The six complete windows, **2026-09-30 1215z, 1230z, 1245z, 1300z, 1315z,
1330z**. Also present in that file and excluded for a different reason:
2026-09-30 1345z and 2026-10-02 0400z, both **truncated** before their T−90
deadline.

**No rule below may ever be scored on these eight windows.** They produced the
facts the rules are built from; scoring on them would be circular.

## The only two discovery facts the rules may use

1. **Time remaining at entry separates.** Eventual FORCED lots were opened with
   a mean **389.8 s** remaining; eventual QUALIFYING lots with **466.1 s**.
   −2.02 sigma at h=1, strengthening monotonically to −6.65 at h=120. This is
   knowable *before* entry.
2. **Early executable mark deterioration separates.** At h=5 s the mean
   `first_leg_move` was **−0.0160** for QUALIFYING and **−0.0442** for FORCED
   (−4.76 sigma). Note this is one signal reported four ways — pair cost,
   distance-from-ceiling, unwind P&L and complement move are algebraic
   restatements of it.

Spread, executable depth and recent trade flow are **excluded**: none exceeded
±0.8 sigma reliably, and flow flipped sign across horizons.

## Cutoffs — coarse, round, and NOT optimized

Every value below is a round number chosen to **bracket** the observed discovery
means, deliberately including values on both sides so the grid cannot be read as
a fit. No P&L was consulted in choosing them and none will be.

### Rule A — entry-time restriction

Accept a first leg only if, at the moment of the fill,
`time_remaining_s ≥ A` where

    A ∈ {300, 420, 540}

(5, 7 and 9 minutes. The discovery FORCED/QUALIFYING means, 390 and 466, fall
*between* 300 and 540; 420 sits between them. All three are reported.)

### Rule B — early adverse-move guard

After a first-leg fill, at a fixed check horizon `h` seconds, if
`first_leg_move ≤ −X` then close the lot immediately at the evidenced executable
price. Otherwise the baseline policy continues untouched.

    h ∈ {5, 10}          X ∈ {0.01, 0.02, 0.03}

(1, 2 and 3 cents. The discovery means at h=5 were −0.0160 and −0.0442, so the
grid brackets both.) The guard fires **at most once per lot**, at horizon `h`
only; it is not a trailing stop.

### Rule C — combination

Rule A **and** Rule B applied together. To keep the grid small and avoid an
implicit search, only **one** combination is pre-registered:

    A = 420 s,  h = 5 s,  X = 0.02

(the mid value of each grid, chosen for being the midpoint and for no other
reason.)

### Full pre-registered set: 13 configurations

| id | rule |
|---|---|
| `BASELINE` | PAIR_OR_UNWIND unchanged |
| `A300`, `A420`, `A540` | entry-time restriction |
| `B5-1c`, `B5-2c`, `B5-3c` | guard at h=5 s, X = 1/2/3¢ |
| `B10-1c`, `B10-2c`, `B10-3c` | guard at h=10 s, X = 1/2/3¢ |
| `C` | A=420 ∧ h=5 ∧ X=0.02 |
| *(reported for completeness)* `A300+B5-2c`, `A540+B5-2c` | sensitivity of C to A only |

All 13 are reported on every new window regardless of outcome. **No cutoff will
be added, removed or altered after new-window results are seen.** If the grid
turns out to be badly placed, that is reported as a finding, not fixed.

## Everything else is held fixed

Ceilings stay the pre-registered .99/.98/.97/.95. Fill discipline unchanged: no
touch=fill, evidenced queue consumption only, no beyond-touch queue, UNSCORABLE
trades fill nothing, book freshness ≤ 2000 ms. Deadline T−90. q ∈ {5, 25, 100}.
Recorder untouched; book-row cap not raised; pending Railway EnvironmentPatch
not applied.

## Metrics to be reported per candidate, per window

first legs accepted / rejected · qualifying exits · forced exits · **false
rejects** (legs the candidate rejected or closed early that BASELINE later
closed profitably) · forced losses avoided · profitable exits sacrificed · net
P&L · net per accepted leg · net per window · worst lot · worst window ·
unresolved % · capital required.

`false rejects` is computed by running BASELINE on the identical tape and
matching lots by (slug, side, entry price, entry timestamp), so the comparison
is lot-for-lot rather than aggregate.

## Success criterion, stated in advance

A candidate is interesting only if, on windows **not** in the discovery sample,
it beats BASELINE on net per accepted leg **and** does not do so purely by
rejecting almost everything. A candidate that accepts fewer than half the
baseline's first legs is reported but treated as a capacity reduction rather
than an improvement.
