# Pre-registration: the single post-decoder evaluation

**Written 2026-09-22T23:50Z, BEFORE the decoder gate resolved and before any
evaluation was run.** Pre-registered so the design cannot be chosen after
seeing which design flatters the result. Owner-directed 2026-09-22.

**Status of the Gemini short-crypto hypothesis: OPEN. Not failed.** Nothing in
P8, P9, P10 or P11 has falsified it. What has been established is that the
historical tape cannot be decoded without contemporaneous books, which makes
every side-dependent result provisional — a measurement problem, not a verdict.

## Scope, and what is forbidden

The evaluation tests **three configurations and no others**:

| arm | what differs | why it is admissible |
|---|---|---|
| **CONTROL** | the frozen lane, unchanged | the reference |
| **H1** | `TTE_policy` [0,300] → [0,900] on BTC15m | restoration of measured L5 behaviour |
| **H2** | `EXEC_A_PLUS_B` placement (one tick better, refused if it would reach the ask; keep a rung holding its place when the touch moved ≤1 tick) | already-evidenced, exposure-matched |

**Forbidden:** any search over bands, tick offsets, price floors, pair-cost
ceilings, spread gates, residual caps, action budgets or any other threshold.
No grid, no sweep, no "while we're here". If H1 and H2 both fail, the answer is
that they failed — not that a fourth arm should be tried. Adding an arm after
seeing results voids this pre-registration and must be recorded as a new one.

## H1 — BTC15m participation-band mismatch

`L5_ADAPTED_GEMINI_CRYPTO.TTE_policy = {'quote_between_tte_s': [0, 300]}` is
`OURS_ADAPTED` — never measured from L5 — and **300 is the length of a Gemini
BTC5m contract**. It was chosen for the 5-minute cohort and inherited unchanged
by BTC15m. L5's own measured horizon is **[0, 900]**.

**This is restoration of measured L5 behaviour, not parameter optimization.**
[0,900] is not being selected because it performs better; it is what the source
behaviour actually is, and [0,300] is a borrowed constant from a different
instrument. Had the transfer been done correctly the first time, [0,900] would
have been the control.

Measured so far (participation only, and see the correction below): the control
rests 17–37 rung-cycles per BTC15m window against the corrected arm's 67–149 —
a **4–7.5× exposure difference**. Fills scale roughly linearly with it, so the
first version's "6.2× effect" was largely definitional and is **withdrawn**.

**To quantify once decoding permits:** participation (rested rung-cycles and
quoting seconds per window), fills, and then the full economics below. The
count of fills is not the finding; the economics are.

## H2 — passive-placement starvation

The control rests at `tick_offsets = [0]`, i.e. **at** the bid. `EXEC_A_PLUS_B`
rests one tick better, refusing any improvement that would reach the ask, and
keeps a rung that still holds its place when the desired touch moved by at most
a tick.

Observed at **approximately equal exposure** (18 vs 19 rested rung-cycles in the
sampled window; both arms run the same [0,300] band, which never binds on a
300s contract): BTC5m first fills **47 → 155**, completions **6 → 52**. Unlike
H1 this comparison is not exposure-confounded.

**H2 must not be judged on fill count.** Paying one tick better buys queue
position and pays for it in basis. The evaluation decides H2 on:

* net pre-rebate P&L;
* matched-pair economics, separately;
* residual/directional economics, separately;
* effective pair cost (`avg_yes + avg_no`), distribution not mean;
* inventory recycling — does the extra fill get complemented, or does it become
  carried residual?

A version of H2 that triples fills and raises effective pair cost above 1.00 has
made the lane worse, and that is the expected failure mode to look for first.

## The envelope: **UNTESTED AT RELEVANT INVENTORY DEPTH**

The P11 conclusion is corrected to this wording and no stronger. The envelope
caps were not exonerated; the sample had no power to test them.

`MAX_IMBALANCE = ACTION_SIZE × MAX_SAME_SIDE_RUN` = 0.01 × 4 = 0.04, so tripping
it needs **four net same-side fills in one window**. Observed across 151 live
shadow windows:

| fills in a window | 0 | 1 | 2 | 3 | ≥4 |
|---|---|---|---|---|---|
| windows | 125 | 18 | 7 | 1 | **0** |

**Two pieces of prior evidence say the caps are NOT harmless**, which is why
"untested" is the right word rather than "inert":

1. **On L5's own history the cap bites.** Measured over 2,216 windows and
   35,576 same-side actions, q-normalized (source: the `LIVE_Q1_BTC15M_CAP4`
   note in `live_envelope.py`): peak imbalance per window **p50 2.76, p75
   5.21**; cap coverage **k=2 → 38.2%, k=4 → 66.9%**. So **CAP4 truncates about
   a third of L5-like windows**, and L5 extends while already holding ≥2 units
   in **82.1%** of same-side actions.
2. **On our own replay corpus the cap's bite has a located edge.** In the P2
   sequential replay over 11 windows (`P2_REPLAY_OVERLAYS.md`), CAP4 was
   **byte-identical to baseline** (58 fills) while **CAP3 was not** (57 fills),
   CAP2 gave 52 and CAP1 gave 38. The first alteration therefore sits between 3
   and 4 on that corpus.

The gap between (1) and (2) is the whole point: our fill rate is far below
L5's, so a cap that would truncate a third of L5's windows never fires on ours.

**Required measurement, once trustworthy replay produces ≥4-fill sequences:**
for each of `MAX_IMBALANCE`/CAP4, `MAX_YES`/`MAX_NO` (max-leg), `MAX_TOTAL_SPEND`,
`MAX_ACTIONS`=12 and `max_working_orders`=2, report

* the **first action in the sequence at which the constraint would alter L5's
  next action**, by index and by inventory depth;
* what that action would have been (`OPEN`/`EXTEND`/`COMPLETE`) and what it
  became;
* the fraction of sequences in which the constraint ever alters anything;
* and the economics of the altered continuation against the unaltered one.

Until such sequences exist, no claim is made in either direction.

## Trajectory fidelity is a first-class result, not a footnote

**A configuration that makes money while behaving nothing like L5 is not
evidence of replication and will not be reported as such.** The evaluation
reports the following distributions for every arm, beside the economics, and
compares them to L5's source behaviour.

Source: `RESIDUAL_EXIT_FORENSICS_V1` — BTC15m, 5,546 windows, 693,916 fills,
zero sells, 38,180 reconstructed residual episodes, chronological TRAIN/OOS.

| trajectory statistic | L5 source value | how ours is scored |
|---|---|---|
| fills per window | (dense; ours is 0.29 BTC5m / 0.05 BTC15m today) | full distribution, not mean |
| same-side run length | P(EXTEND) = **0.66** unconditional → expected run **≈2.9**; run 4+ → **0.733** OOS | distribution + P(EXTEND \| run=k) for k=1..5+ |
| EXTEND→COMPLETE transitions | P(opposite next \| holding k) **flat**: .479 .481 .503 .487 .490 for k=1..5+ | same table; flatness is the test |
| imbalance episode termination | **84.3% FLIPPED** (overshoot through zero), **1.2% COMPLETED_FLAT**, **1 of 5,546** windows ended flat | share flipped / flat / carried |
| peak inventory depth per window | **p50 2.76, p75 5.21** (q-normalized) | same quantiles |
| carried residual | median **141.8 shares = 7.7%** of window volume, p90 462 | same, as share of window volume |

**Interpretation rules, fixed now:**

* An arm that drives inventory to flat is **diverging from L5**, not improving
  on it. Flat completion is L5's rarest and most expensive exit (median episode
  pair cost 0.9950 against 0.980–0.986 for flips) and he was abandoning it
  chronologically (15.4% in March → 0.32% in August).
* An arm whose `P(EXTEND | run=k)` ordering breaks is diverging, because run
  length is the only predictor that kept its bin ordering out of sample.
* An arm that uses `sign(Δp)` or favored-side accumulation is disqualified on
  sight: both are falsified (|Δp| predicts extension, sign does not; favored-side
  P = 0.4919 TRAIN / 0.4847 OOS).
* **Positive P&L with a divergent trajectory is reported as a NEGATIVE
  replication result**, whatever the money says.
* Late incentive-era L5 behaviour (same-side runs mean ≈3.11, p90 ≈7,
  P(EXTEND\|EXTEND) ≈0.695, near-$1 pair tolerance) stays **LOCKED** and is not
  a target.

## Accounting discipline

Reported separately and never summed: matched-pair economics · directional
residual P&L · spread capture · maker rebates · liquidity rewards · fees ·
settlement cashflows. **Rebate economics are added only AFTER arm selection is
complete** and never enter selection. Labels stay
`historical replay → shadow → submitted → filled → settled → credited →
realized`. Nothing is REALIZED without an attributable cash movement. The venue
position representation remains UNRESOLVED, so matched inventory, matched
pairs, pair cost and total spend are never reported as Gemini venue state.

## Preconditions

1. P10 decoder gate G1–G5 all PASS, unchanged and un-reinterpreted;
2. decoder frozen by content hash;
3. BTC5m/BTC15m reconstructed with the frozen decoder;
4. this evaluation run once, on causally available information only;
5. its accounting recomputed independently;
6. rebate added afterwards on its own line;
7. the 09-23+ holdout untouched.

**No live orders until the resulting q=1 manifest is approved.** If the gate
fails, this pre-registration survives it: the two hypotheses remain the
highest-priority configuration questions for whenever decodable data exists,
whether that is on Gemini or elsewhere.
