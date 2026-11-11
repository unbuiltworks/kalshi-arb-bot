# P11 corrections — three numbers that did not survive challenge

Written 2026-09-22T23:35Z, after the owner said the numbers were not credible.
They were right about two of the three headline claims and about the framing of
the third. What follows is what I checked, what broke, and what is left.

## First, what I verified and what held

**The transcription is exact.** `l5f_actions_20260922.tsv` was hand-transcribed
by me from a Railway log query, which is the weakest link in the chain, so I
re-queried the window 22:00Z–23:20Z independently and compared row by row. All
21 rows match the source on every field — symbol, requested, filled, blocked.
No transcription error was found. (The row count in the header was wrong in the
first version: I wrote 146 before counting, and it is 151. Fixed.)

## Correction 1 — the fill-rate denominator is not what I called it

**Claimed:** "35 fills from 4,396 requests — 0.80%", and "99.2% of requested
actions never become a fill at all".

**Wrong.** `action_census["requested"]` is incremented inside the per-cycle
loop, once per rung the policy wanted **on that cycle**. A single standing
desire is recounted every cycle, and `both_sides_policy` emits a rung for each
of Up and Down, so the counter is roughly `cycles × 2`. Check: the 22:59Z BTC5m
window logs `requested={'OPEN': 46}` against ~23 readable cycles — 23 × 2 = 46.
So 4,396 is **rung-cycles, not intended orders**, and dividing fills by it
produces a number with no natural meaning. "99.2% of requested actions never
become a fill" asserts 4,396 intended orders, which never existed.

**Correct statement of the same fact:**

| | windows | fills | fills/window |
|---|---|---|---|
| BTC5m | 113 | 33 | **0.292** |
| BTC15m | 38 | 2 | **0.053** |

Fills per window is the honest unit. The qualitative point — that fills are
very rare and this is where the funnel narrows — survives; the 0.80% figure
does not, and should not be quoted.

The **1.82% blocked rate does survive**, because `blocked` is counted in the
same per-cycle loop as `requested`, so that ratio is like-for-like. It should
be read as "1.82% of rung-cycles".

## Correction 2 — "the caps never bind" had no power to detect the opposite

**Claimed:** the envelope is inert, therefore it is not what suppresses L5.

**This is the serious one.** `MAX_IMBALANCE` is `ACTION_SIZE × MAX_SAME_SIDE_RUN`
= 0.01 × 4 = 0.04, so tripping it requires **four net same-side fills inside one
window**. Observed fills per window across all 151:

| fills in a window | 0 | 1 | 2 | 3 | ≥4 |
|---|---|---|---|---|---|
| windows | 125 | 18 | 7 | 1 | **0** |

**No window reached four fills of any kind, let alone four net on one side.**
The envelope could not have fired. Observing zero envelope blocks is therefore
guaranteed by construction and carries no information about whether the caps
would interfere at a fill rate high enough to reach them — which is precisely
the regime the audit was commissioned to ask about.

**Corrected claim:** the envelope caps, the 12-action budget and the
working-order cap were **not tested** by this sample. They are untested, not
exonerated. The honest statement of the original question is:

> *Do CAP4 / the action budget / one-rung / max-working-orders terminate the
> oscillator before it can recycle residual?*
> **Unknown from this data.** The oscillator never reached the inventory depth
> at which any of them could act, so this sample cannot distinguish "harmless"
> from "would bind hard if fills improved".

Answering it needs a sample containing windows with ≥4 same-side fills. None
exists yet. The `EXEC_A_PLUS_B` arm produces more fills and is the only
plausible source of such windows.

## Correction 3 — the BTC15m 6.2× is mostly exposure, not efficacy

**Claimed:** correcting `TTE_policy` from [0,300] to [0,900] takes BTC15m from
5 first fills to 31 — "a 6.2× change in participation from a number that was
never a finding about L5 at all".

**The count is right; the framing overstates it.** The band determines how long
the arm quotes at all, so more fills is partly definitional. Measured exposure,
in rested rung-cycles per window, from the `ARM_BAND` lines:

| window | control rested | V2 rested | ratio |
|---|---|---|---|
| BTC15M…2245 | 17 | 127 | 7.5× |
| BTC15M…2300 | 37 | 149 | 4.0× |
| BTC15M…2315 | **0** | 67 | — |

Exposure differs by roughly 4–7.5×, against a fill difference of 6.2×. **Per
rested rung-cycle the two arms are not measurably different.** There is no
evidence here that quoting the earlier part of the window is *better* per unit
of quoting; the arm fills more because it is quoting more.

**Corrected claim:** the inherited [0,300] band means the control quotes only
about a fifth of each BTC15m window, and fills scale roughly linearly with
exposure. That is still a real finding — the band is `OURS_ADAPTED`, 300 is the
length of a *BTC5m* contract, and L5's own measured policy is [0,900], so a
fivefold reduction in participation is being imposed by a constant borrowed
from a different instrument. But it is a **participation** finding, not a
demonstration that the corrected band trades better.

## What survives unchanged

* **END_OF_WINDOW is the only overlay that produced any hold**: 80 holds in 21
  of 151 windows — 58 OPEN, 22 EXTEND, **0 COMPLETE**. The zero for COMPLETE is
  the specified behaviour of `WINDOW_COMPLETE_ONLY` and confirms the phase logic
  suppresses residual creation without suppressing residual reduction. Unlike
  the envelope caps this constraint *was* reachable and *did* fire, so it is a
  measurement rather than a tautology.
* **BTC5m `EXEC_A_PLUS_B` 47 → 155 first fills is NOT exposure-confounded.**
  Both arms run the same [0,300] band, which never binds on a 300s contract,
  and their rested rung-cycles are essentially equal (18 vs 19 in the sampled
  window). That difference is attributable to the one-tick price improvement
  and queue position, as its own note claims (39 of 40 fills rest on a rung with
  nothing in front). It says nothing about profitability: the improvement pays a
  better price, and no pair economics are attached to it.
* **The provenance table.** `rung_count=1` / `tick_offsets=[0]` are TRANSFERRED
  from L5 evidence (73 of 108 parents); `TTE_policy=[0,300]` is `OURS_ADAPTED`
  and is a BTC5m-length artifact. These are facts about the parameter store and
  the code, independent of any count above.
* **The decoder dependency.** Every fill-valued number here runs through
  `tape_replay.reaches`, which reads the aggressor `type` and a mirrored price,
  so all of them remain provisional until the P10 gate settles H1/H2.

## What this does to the audit's conclusion

The original bottom line said the interference is not the caps but the inherited
band and passive placement. After correction:

1. **The caps are untested, not cleared.** Removing that claim removes the
   audit's main reassurance.
2. **The band costs ~5× participation on BTC15m**, with no evidence either way
   about per-opportunity quality.
3. **Placement improvement raises BTC5m fills ~3.3× at equal exposure**, with no
   economics attached.
4. The dominant fact is unchanged and is not a configuration question at all:
   **0.29 fills per BTC5m window and 0.05 per BTC15m window.**
