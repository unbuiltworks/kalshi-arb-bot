# CANDIDATE V1 — frozen, and it fails the advance gate

**Fingerprint `656317d7c52f5717c6a291121c854079648bef655a2954a9a4ccbdf723b2d8c1`**,
computed from the model estimated on **discovery only**, and verified
**identical** with and without the OOS read.

Not a profitability optimization, not a threshold sweep. Every number is
an empirical conditional from the corrected full-corpus pre-incentive
evidence. **No live authorization sought.** Historical reconstruction of
another trader's public activity.

## What was built

Chronological split of 6,252 pre-incentive events: **4,376 discovery /
1,876 sealed OOS**. 157,584 causal decision points in discovery, 47,199
held out. Action space OPEN / EXTEND_SAME_SIDE / SWITCH_OUTCOME / STOP,
state read strictly before each next leg.

**Assumptions removed, as instructed:** no 5¢ EXTEND gate (higher-price
EXTEND is allowed); no requirement that pair cost be <1 at any point; no
13-hour [0.20,0.82] band or 0.991 trigger; TTE uses each event's own
window rather than a fixed 900s.

**Ambiguity not papered over:** legs sharing a timestamp with the opposite
outcome are genuinely unordered in the tape, so they are excluded from
estimation and scoring rather than having an order invented.

## Behavioural fidelity — stable out of sample, and degenerate

| | discovery (n=157,584) | **held-out OOS (n=47,199)** |
|---|---|---|
| overall accuracy | 0.6035 | **0.5996** |
| cell coverage | 99.76% | 99.96% |
| EXTEND_SAME_SIDE | **0.9971** | **0.9970** |
| STOP | 0.0656 | 0.0848 |
| **SWITCH_OUTCOME** | 0.0021 | **0.0000 (0 of 17,096)** |

Discovery → OOS is stable, so there is **no overfit**. But the always-EXTEND
base rate is **0.6027**, so the model adds **0.08pp** of skill, and on
held-out data it predicts SWITCH **exactly zero times out of 17,096.**

### Why, and it is structural rather than fixable by tuning

Of 136 adequately-powered cells (n≥30), **only 5 have SWITCH as the
majority action** and **3 have STOP**. Max P(SWITCH) in any cell is 0.6562
on **n=32**; across the well-populated cells the range is the measured
**32.4% → 42.0%** — real, monotone, non-overlapping at the ends, and
**sub-majority everywhere.** An argmax policy therefore *cannot* emit
SWITCH, by construction.

A stochastic variant reproduces the action **mix** almost exactly
(SWITCH 0.3708 vs 0.3697 actual; EXTEND 0.6022 vs 0.6031; STOP 0.0270 vs
0.0271) but scores only **0.5096** per decision — it gets the distribution
right and the timing wrong.

**Conclusion: L5's switch timing is not predictable from the state the
brief lists as observable.** The one state variable in that list we do
**not** have for Polymarket is *current outcome prices* — the book. That
makes the failure a plausible consequence of missing data rather than of
model form, and it is directly testable once book data exists.

## Head-to-head against the frozen clone — V1 wins decisively on EXTEND

On identical decision points, 94,968 actual L5 EXTEND decisions:

| | CANDIDATE V1 | frozen clone (5¢ gate) |
|---|---|---|
| EXTEND reproduction | **99.71%** | **9.43%** |
| SWITCH | 0.21% | n/a — envelope blocks |
| STOP | 6.56% | n/a |

**The 5¢ gate blocks 90.6% of L5's real EXTEND decisions** (86,011 of
94,968); 39.0% of those next legs were at a *higher* price, which the gate
forbids by design. This is the single most strongly supported change in
the programme — and it is an **authorization** matter, not an edit, since
the 5¢ constant is restated in four live bounds
(`arming.py:112,143,173,198`).

## Advance gate — NOT MET, so no shadow

1. **Materially outperforms the clone on held-out L5 events?** **PARTIAL.**
   Decisively yes on EXTEND (99.70% vs 9.43%); **no** on SWITCH (0 of
   17,096) or STOP. The composite policy does not reproduce L5's
   sequencing.
2. **Cash economics positive on chronological OOS?** **NOT EVALUATED, and
   deliberately so.** A policy that never switches never acquires the
   opposite leg, so it never completes a pair: its economics reduce to the
   directional residual alone, which is measured at **−$30,654** across
   the corpus. Computing a cash figure for a policy whose only possible
   behaviour is the corpus's single largest loss term would produce a
   number with no decision value. Stated rather than manufactured.
3. **A lawful venue meets the microstructure requirements?** **PENDING** —
   derived separately; Gemini BTC5M is closed and not reopened here.

**Therefore: no advance to realtime shadow.** One of three conditions is
partial, one is moot, one is open.

## What IS established and worth carrying

* **Higher-price EXTEND must be permitted.** 90.6% of L5's EXTENDs are
  unreachable under the 5¢ gate; permitting them lifts reproduction from
  9.43% to 99.70%.
* **The action mix is reproducible; the timing is not.** A stochastic
  policy matches all three action frequencies to within 0.0011.
* **Switch timing needs information we do not hold.** This converges with
  the P0 data task: without the book around L5's fills we can neither
  predict its switches nor tell whether its higher-price extends were
  executed against genuinely available liquidity.

## Not done, per the kill rules

No D V3. No threshold sweep around the clone. No reopening of Gemini
BTC5M from the three-trade chained candidate. No deployed parameter
changed. OOS was read only after the fingerprint was fixed, and the
fingerprint is unchanged by that read.
