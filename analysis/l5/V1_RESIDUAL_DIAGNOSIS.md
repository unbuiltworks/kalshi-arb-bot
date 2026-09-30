# L5-ADAPTED INVENTORY ENGINE V1 — RESIDUAL DIAGNOSIS

**Cohort: the 49 OOS windows, CONSUMED. Diagnostic only, never again validation
evidence.** Clip q10, Variant C engine frozen at `967cf1ee`. No threshold
introduced, nothing fitted, no parameter searched. θ, clip, residual_cap_k and
hold_s are read from the frozen engine.

Artefact: `analysis/l5/v1_residual_diagnosis.json`. Code:
`scripts/l5_v1_residual_diagnosis.py`.

**39 of 49 windows carried a stranded residual to settlement**, costing
**−$2.4603/window**. That is the whole target of this diagnosis.

---

## A. Persistent maker — what happened to a complement resting at the cap

The complement is priced from the held side's all-in basis and left in place.
Classified from the contemporaneous full-depth ladder and the attributable
prints, recomputing the cap at every step so it tightens when the held side
grows and is never taken from the future.

| what happened next | windows |
|---|---|
| **traded through** — the market sold the complement BELOW our cap | **19** |
| never reached | 9 |
| displayed only — offered at/below cap, no attributable print | 8 |
| traded at — printed exactly at our cap | 3 |

**Plausibly fillable after queue: 14 of 39 at q10, and the same 14 at q20 and
q30.** Clip size is not the constraint — where a fill was available it was
available for 30 contracts as readily as 10.

The single most important number here is **19 traded through**. In half the
stranded windows the market came and sold the complement at a price our own
inventory could afford, after we had stopped being there.

**The 8-window gap between "traded through" and "fillable" is not a market
fact — it is our fill model refusing to credit a fill because our price level
was not displayed in the ladder.** That is the fail-closed queue branch from the
§7 fix. It is the conservative direction, but it means A's true reach is
somewhere between 14 and 22 windows and this diagnosis cannot narrow it further
from this tape.

## B. Continuous inventory — cheaper same-side appearing while we wait

Credited only where the cheaper acquisition would itself have filled on an
attributable print. Display alone is never treated as a fill.

- cheaper same-side inventory fillable on a print: **12 of 39**
- resulting cap improvement: **median +0.075, max +0.34**
- and that improvement made the observed best complement acceptable: **11 of 12**

So when cheaper inventory appears, lowering the aggregate basis nearly always
converts an unreachable complement into a reachable one. This is the mechanism
Variant C forbids: `extend_requires_balance` refuses exactly these acquisitions.
The refusal bought the tail bound and cost this.

## C. Speed — opportunities too brief for the current path

67 opportunity episodes across the 39 windows.

| measure | value |
|---|---|
| episode duration, median | **0.0 s** (single observation) |
| duration p90 / max | 31.6 s / 33.0 s |
| zero-length (single-tick) episodes | **37 of 67** |
| traded volume inside an episode, median | **5,513 contracts** |
| displayed at/below cap, median | 1,806 contracts |

**Liquidity is not the constraint.** A median episode carries 5,513 traded
contracts against our 10-contract clip — three orders of magnitude of headroom.

**Honest limitation, and it bounds what C can be credited with: this tape cannot
measure C.** Most episodes are a single observation, and an order that is
*already resting* catches a single-observation opportunity without needing to
react at all — so those episodes are evidence for A, not for C. A genuine speed
deficit would be an opportunity occurring strictly between two observations,
which by construction is invisible in a tape made of those observations.
**C is therefore credited $0 here, not because speed does not matter but because
this data cannot see it.** Measuring it requires the forward shadow's own
full-depth frames at their native cadence.

## D. OPEN — loss embedded before any waiting

| measure | value |
|---|---|
| windows where a pair formed from flat | 10 of 49 |
| first pair cost: median / min / max | **0.9300** / 0.7500 / 0.9600 |
| first pair above θ=0.96 | **0** |
| first pair above $1.00 | **0** |
| first-acquisition implied complement cap, median | 0.42 |
| windows whose first acquisition left a cap ≤ 0.05 | **2** |

**This is the finding that overturns a prior expectation.** The ~0.99 flat-state
pair cost measured on the *training* tape as a tick-level property does **not**
show up as realised OPEN cost here: the median first pair actually formed cost
**0.93**, and not one exceeded θ. The two-sided quote is frequently at 0.99, but
the pairs that actually *form* form at much better prices, because both legs
rarely fill at the touch at the same moment. **D is a real mechanism in only 2 of
49 windows** — those where the first leg went on at ~0.95, leaving a complement
cap of 0.01 that no market was ever going to fill (1415z is the case: cap 0.01,
best price ever seen 0.05).

I had flagged the ~0.99 flat-state issue as a known scope limitation likely to
cost real money. On this evidence it does not, and the tick-level measurement
overstated it.

---

## Ranking by recoverable dollars per window

| rank | mechanism | windows it could change | recoverable | evidence quality |
|---|---|---|---|---|
| **1** | **A persistent maker** | 14 (of 39) | **+$1.2872/window** | **Good but optimistic** — direct prints below our own cap, at real depth. Assumes the full residual fills at the best observed price; the queue model credits whole-interval volume at a non-touch level, which can only create fills. True value likely lower. Reach may extend to 22 windows if the 8 fail-closed cases were real. |
| **2** | **B continuous inventory** | 7 | **+$0.6613/window** | **Good** — requires an attributable print for the cheaper acquisition, so it is not a display artefact. Conservative in that it credits only one clip of improvement. |
| 3 | C speed | unknown | **$0, unmeasurable here** | **Absent** — this tape cannot distinguish a speed deficit from an absence of opportunity. Needs forward frames at native cadence. |
| 4 | D open economics | 2 | **$0** | **Good** — measured directly; the loss is embedded at the first fill and no later mechanism reverses it. Small in this cohort. |
| — | unrecoverable | 3 | $0 | price genuinely never returned |

**Total recoverable: +$1.9486/window.**

### What that means for the ≥$5 objective

Variant C's realistic net was **+$0.0561/window**. Perfect, frictionless
recovery of everything this diagnosis identifies gives a ceiling of
**≈ +$2.00/window**, leaving **−$0.51/window** of residual loss that no
identified mechanism reaches.

**So the diagnosed mechanisms cannot reach +$5/window at q10 on this cohort, even
if realised in full.** They close about 40% of the gap. The remaining distance
is the margin-versus-scale problem quantified earlier: at L5's own measured
BTC-15m trading margin of 3.7 bp, $5/window requires roughly $13,500 deployed
per window against our $81 envelope. V1 should be built to capture A and B —
they are real, they are the largest measured effects, and A in particular is
well-evidenced — but it must not be presented as a path to $5/window at this
clip.

### Priority for V1 implementation

1. **A — keep the complement resting.** Variant C priced it correctly and then
   let the queue model and the requote logic take it away. The mechanism is
   already there; what is missing is persistence.
2. **B — permit bounded cheaper same-side extension.** This directly contradicts
   `extend_requires_balance`, which is what bought the tail bound. The two must
   be reconciled rather than one simply replacing the other: extension should be
   permitted when it *lowers* the aggregate basis and refused when it raises it,
   which is a different rule from refusing all extension while imbalanced.
3. **C — instrument, do not implement.** Measure the real cadence from the
   forward shadow before assuming speed is worth anything.
4. **D — leave alone.** 2 of 49 windows, and the tick-level version of this
   concern was overstated.

This ranking is diagnostic prioritisation. It is **not** a backtested strategy
result, and none of these numbers may be quoted as V1 performance.
