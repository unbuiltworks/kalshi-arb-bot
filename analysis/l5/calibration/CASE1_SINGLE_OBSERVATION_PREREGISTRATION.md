# Case 1: ONE live calibration. Preregistered before arming.

**AUTHORIZED** by the owner, superseding the six-window / $1.50 plan in
`B_CALIBRATION_PREREGISTRATION.md`, which is **withdrawn** — not deferred.

## Why one probe, and why now

The A-vs-Case-2 decomposition (`analysis/l5/A_VS_CASE2_ATTRIBUTION.md`) left
exactly one surviving candidate and exactly one unresolved fill-semantic
question bearing on it:

* V3 is closed. Its entire advantage over V2 was the impossible
  marketable-replenishment / absent-depth queue grant (pure Case-2 effect
  −$0.4479/window, t = −3.45).
* **V1_EXTEND_ONLY** is bit-identical across the Case-2 repair, so its
  +$0.3146/window is measured *after* the accounting correction. Spent-window
  forensic evidence, 18 windows, not validation.
* V1 rests at the touch. **Case 1 — what the venue does to an already-resting
  post-only maker that the market later reaches or crosses — is therefore the
  one unknown that can materially change how V1 is modelled.**

We are not perfecting a simulator in the abstract and not rescuing V3.

**+$0.3146/window is nowhere near the $3–$5/window target.** It is simply the
first figure that survived the execution and accounting cleanup, which is what
makes one tiny calibration worth its cost and nothing more.

## The envelope, as authorized

| parameter | value |
|---|---|
| quantity | **1** |
| markets | **1** |
| submissions | **1**, total |
| outstanding orders | **1** |
| order entry | post-only (`participateDontInitiate`), GTC, genuinely non-marketable |
| repricing / chasing / replacement | **none**, structurally absent from the runner |
| entry price cap | **$0.25** in the side's own terms, else **REFUSE** |
| maximum possible first-leg loss | **$0.25** |
| automatic repeat | **none** |
| after a fill | carry the single contract to settlement |

Enforced in `src/pmus_case1_probe.py` and pinned by `tests/test_case1_probe.py`
(`MAX_ENTRY_PX = 0.25`, `MAX_LOSS = 0.25`, `QTY = 1`), so widening risk later
fails the suite rather than reaching the venue.

### The price rule, and why it must be near the touch

One tick below the own-terms best bid of the **cheaper** side, refusing if that
price exceeds $0.25.

The schema probe deliberately sat 20 ticks away so a fill was impossible. Case 1
needs the opposite: non-marketable at submission, then plausibly **reached** by
natural movement, with us never moving. So the rule refuses in **both**
directions — above the cap (widens risk) **and** by drifting further from the
market to satisfy the cap (buys a near-certain null). The cheaper side is chosen
because the cap can only be met there, not because cheapness predicts anything:
the "buy the cheap side" hypothesis was tested and rejected at 55.8%.

A sweep over all 4,851 two-sided books asserts the chosen price is strictly
below our own-terms ask, on grid, and at or under the cap.

### No complement leg

Exposure is already bounded at $0.25, which **is** the authorized maximum, so no
complement is *needed to bound exposure* and the authorization's "otherwise
settle the single q1 position" applies. One fewer money-moving path, one fewer
taker fee, nothing to reconcile. Stated explicitly because the authorization
permitted a fee-safe complement and we are declining it.

## A qualifying observation requires ALL FOUR

1. `rest_proved` — private frames naming our order **and** REST `OPEN`: we were
   actually resting, not merely accepted;
2. `reached` — **ORDERED**, and this is the whole point of the experiment: the
   own-terms best **ask** fell to at or below our price **after** private NEW +
   REST `OPEN`, with our order unchanged. The reach state is sampled as a
   BASELINE at the instant rest is proved; if the book was already at or through
   our price then, no later observation can establish the ordering and the
   verdict is `AMBIGUOUS_EVENT_ORDER`, not a Case-1 result;
3. `private_terminal` — private evidence plus a proved terminal state, so the
   venue has said what happened;
4. `public_sequence` — public book updates and the recorded transitions, enough
   to order the event.

**A null is a result, not a failure.** If the book never reaches us the label is
`NULL_NOT_REACHED` and we did not chase it. `CASE1_OBSERVED_NOT_FILLED` — crossed
and still unfilled — is a genuine Case-1 answer, not a malfunction.

### Two vocabularies, deliberately separate

**Process `outcome`** drives the exit code: `REFUSED` · `STOPPED` · `ERROR` ·
`NULL_NOT_REACHED` · `CASE1_OBSERVED_FILLED` · `CASE1_OBSERVED_NOT_FILLED` ·
`CASE1_INCONCLUSIVE` · `STOPPED_REST_NOT_PROVED`.

**Evidence `verdict`** — the six mutually exclusive labels the live report must
distinguish, so that *no fill* and *no opportunity* can never collapse into one
bucket:

| verdict | meaning |
|---|---|
| `RESTING_NOT_REACHED` | null. We rested, the market never came, we did not chase |
| `REACHED_OR_CROSSED_UNFILLED` | **qualifying observational result** |
| `FILLED_AS_MAKER` | |
| `FILLED_AS_AGGRESSOR` | would be a surprising and important finding on a post-only order |
| `AMBIGUOUS_EVENT_ORDER` | event order or our role unestablished. **Do not overinterpret** |
| `PROBE_FAILURE` | any safety or lifecycle deviation. Never an evidence claim |

Success here is **observational, not trading** success. A refusal carries no
verdict at all, and the verdict never feeds the exit code — safety does. No label
says realized, profit, validated or OOS, and a test asserts it.

### Maker vs aggressor needs TWO agreeing signals

The role is only called when two independent signals agree, because neither is
sufficient alone:

1. the venue's `aggressor` enum, mapped through an **UNVERIFIED** orientation
   rule relative to our `ORDER_ACTION_BUY` — the 2026-10-01 schema run saw this
   field only on NEW/CANCELED, where it was empty, so **no real fill has ever
   established the mapping for our account** (`AGGRESSOR_RULE_VERIFIED = False`);
2. `commissionNotionalCollected` against `pmus_fees.taker_fee(..., cents=True)`,
   the venue's own statement rounding — economically grounded and independent of
   the enum's orientation. Nothing charged, or a credit, is maker evidence; a
   charge matching the taker schedule is aggressor evidence.

If they disagree, or only one is available, the role is `UNRESOLVED` and the
verdict is `AMBIGUOUS_EVENT_ORDER`. A commission we cannot account for is not
evidence of anything.

## Captured, verbatim

**Private**, per authorization: `type`, `tradeId`, `aggressor`, `transactTime`,
`lastShares`, `lastPx`, `commissionNotionalCollected`, `cumQuantity`,
`leavesQuantity`. Each is recorded as seen, and a field that never appears is
reported `UNAVAILABLE` rather than defaulted. The 2026-10-01 schema run proved
`tradeId` **exists** but was empty on NEW/CANCELED, so that a fill populates it
is still unestablished.

**Public**: raw frames verbatim, the folded book, per-trade `px`/`dir`/`qty`/
`tradeTime`, BBO and depth immediately before, at the moment of reach, and after,
plus every book transition stamped wall + monotonic.

**Correlation**: public prints that could have hit us — correctly directed by the
venue's own `taker.side` (`up` consumed only by SELL, `down` only by BUY,
established on 6,874 trades) and at an own-terms price at or below ours.
Direction is never inferred from price when the venue states it, and an unknown
direction matches nothing.

## Mandated stops

UNKNOWN terminal · stale feed (no public frame for 20 s) · private-stream failure
· unexpected order state (any REJECT/CANCEL we did not request) · reconciliation
mismatch · `OBSERVE_MAX_S` 420 s · within 90 s of the window close.

**Zero open orders must be proven before exit.** The cancel/sweep lives in
`run()`'s `finally`, asserted by AST so no exception can leave an order resting.
The exit code is a **safety** signal: 0 only on a preregistered terminal label
with the account provably clean. A refusal exits **nonzero**, so a one-shot that
fails to arm can never be read as "ran fine".

## Arming

`PMUS_Q1_LIVE` must be the literal `YES`, and `PMUS_Q1_SESSION_ID`,
`PMUS_Q1_ARM`, `PMUS_CASE1_ARM` must all equal the same session id. Four
independent values; three of four refuses.

## The three admissible conclusions

1. **B repaired** — the observation establishes maker/aggressor treatment and
   queue consumption for a crossed resting order; the fill model is corrected.
2. **B partially constrained** — the unresolved portion gets CONSERVATIVE replay
   semantics (`UNRESOLVED`, never an invented fill).
3. **A specifically named second calibration is necessary** — named precisely,
   with its own minimum design.

"Build more infrastructure" is not a fourth outcome. **No second live
calibration without reviewing this evidence first.**

## Then, in this order

repair B fully → rerun the corrected forensic replay → **fresh** OOS → shadow →
bounded live economic probe. Nothing from this probe may be called realized
profit, and no strategy parameter, V1 logic or residual cap changes before the
observation.
