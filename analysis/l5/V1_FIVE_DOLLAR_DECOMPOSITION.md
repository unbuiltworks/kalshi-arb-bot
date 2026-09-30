# CAN AN L5-ADAPTED ENGINE PLAUSIBLY REACH ≥$5/WINDOW?

**Answer: not as this architecture stands, and the binding constraint is not
capital — it is inventory decisions, with capital binding second only in the
hypothetical where those decisions are fixed.**

Cohort: the 49 OOS windows, **permanently consumed, diagnostic only**. Nothing
here is validation, nothing is tuned, no threshold introduced. All figures
$/window over 49 windows. Incentive is the credited maker rate only
($0.003515/contract, 25 sourced prints) and is always on its own line.

---

## 1. The explicit additive arithmetic you asked for

Every term is measured, not assumed. Terms are mutually exclusive by
construction — `attribute()` assigns each window to exactly one mechanism.

| term | q10 | evidence |
|---|---|---|
| current realistic net (Variant C) | **+0.0561** | measured, held-out |
| \+ OPEN improvement (D) | **+0.0000** | 2 of 49 windows; loss embedded at first fill, unrepairable later |
| \+ maker-fill improvement (A) | **+1.2872** | 14 windows fillable after queue; 19 traded through the cap |
| \+ inventory-management improvement (B) | **+0.6613** | 7 windows; cheaper same-side fillable on an attributable print |
| \+ latency improvement (C) | **+0.0000** | not measurable from this tape — see §4 |
| \+ confirmed incentives | *already inside the +0.0561* | +0.1205 of it |
| **= projected realistic net** | **+2.0047** | |
| gap to $5 | **−2.9953** | mechanisms close **40.6%** of the 4.80 gap |

## 2. The same arithmetic at larger clip — this is the part that matters

The recoverable mechanisms **do** scale with clip. The base worsens at the same
time, so the projection rises but sub-linearly:

| clip | cash/win | peak cash | Variant C realistic | recoverable | **projected** |
|---|---|---|---|---|---|
| q10 | $25.05 | $65.40 | +0.0561 | +1.9486 | **+2.0047** |
| q30 | $54.55 | $146.77 | −0.2544 | +3.3692 | **+3.1148** |
| q50 | $79.31 | **$244.77** | −1.0867 | +5.2276 | **+4.1409** |

Extrapolating the q30→q50 slope (+$1.03 per 20 contracts of clip), **$5/window
arrives around q67 at roughly $330 of peak exposure** — beyond the ~$250
account, and 4× the authorised $81 envelope.

So the honest statement is: **≈$4/window is conceivable at q50 if and only if A
and B are realised in full at the optimistic fill assumptions below. $5 requires
capital we do not have.** And the optimism is material — see §6.

## 3. Edge per deployed dollar — the measured picture, with no hypothetical

This is the number that decides "undercapitalized or no scalable edge":

| clip | arm | realistic net | edge per $ deployed |
|---|---|---|---|
| q10 | baseline | +0.1993 | **+0.383%** |
| q10 | variant_c | +0.0561 | +0.224% |
| q20 | baseline | −0.5100 | **−0.580%** |
| q20 | variant_c | −0.1486 | −0.360% |
| q30 | baseline | −0.8724 | **−0.740%** |
| q30 | variant_c | −0.2544 | −0.466% |

**As built, edge per dollar is positive only at the smallest clip and goes
negative the moment size increases — monotonically, in both arms.** That is not
an undercapitalized edge. Deploying more capital into the engine *as it exists
today* destroys money faster. Any capital argument is therefore conditional on
first fixing the inventory decisions, never a substitute for it.

## 4. Constraint separation

| candidate constraint | binding? | evidence |
|---|---|---|
| **bad inventory decisions** | **YES — dominant** | A+B recover +1.95 (q10) to +5.23 (q50)/window. The losses are decisions, not conditions. |
| insufficient edge per dollar | **YES, as built** | positive only at q10; −0.58% at q20, −0.74% at q30 |
| insufficient deployed capital | **only at the ceiling** | $5 needs ≈$330 peak vs $250 account and $81 envelope. Irrelevant until the edge scales. |
| insufficient fill / capacity | **NO** | median opportunity episode carries **5,513 traded contracts** against a 10–50 contract clip; fillability identical at q10/q20/q30 |

**Capacity is not the problem and capital is not yet the problem. The engine is
losing money it could have kept.**

## 5. Mechanism ranking, with L5 provenance

| mech | $/win (q10) | windows | L5 status | note |
|---|---|---|---|---|
| **A persistent maker complement** | **+1.2872** | 14 of 39 | **L5-supported** | Spring L5 is OPEN → EXTEND → **WAIT** → COMPLETE, completing in the 0.95–0.96 band. Waiting at a basis-derived price *is* the documented behaviour. |
| **B bounded continuous inventory** | **+0.6613** | 7 of 39 | **L5-supported** | Same-side EXTEND is first-class in L5 (`P(EXTEND\|EXTEND)≈0.695` mature). Restricting it to acquisitions that *lower* aggregate basis is a **deliberate adaptation**. |
| C latency / event-driven | **0.0000** | unknown | L5-compatible/unknown | L5's mature opposite-side wait falls to seconds, but we cannot measure our own deficit from this tape. |
| D OPEN economics | 0.0000 | 2 of 49 | L5-supported | L5 targets materially cheaper completion; our realised OPENs already do (median first pair **0.9300**). |

**Correction to a prior expectation of mine:** I flagged the ~0.99 flat-state
pair as likely to cost real money. It does not. The median first pair that
actually *formed* cost 0.9300 and none of ten exceeded θ — both legs rarely fill
at the touch simultaneously. The tick-level measurement overstated it, and D is
real in 2 of 49 windows only.

## 6. Why these numbers are upper bounds

Stated plainly because the conclusion depends on it:

1. **A is priced with hindsight.** `best_own_terms_seen` is the best price over
   the whole remaining window. A defensible price (first fillable moment, or
   VWAP at the cap) will be worse.
2. **The queue model is generous at a non-touch level** — it subtracts
   whole-interval volume from the queue ahead of us at our own level, crediting
   volume that may have executed at better prices and never reached us. A capped
   complement is by construction usually away from the touch.
3. **A assumes the entire residual fills at one price**, without checking depth
   against quantity at every step.
4. **C is credited $0, which is not the same as "worth $0"** — 37 of 67
   opportunity episodes are a single observation long, and this tape cannot
   distinguish "too fast for us" from "never happened".

An independent reviewer is attacking exactly these four points. **If A survives
at even half its face value the ranking is unchanged; if it collapses, the
architecture has no path to $5 at any clip we can fund.**

## 7. Smallest evidence-supported V1 architecture — NOT implemented

Three changes to the frozen Variant C engine, nothing more:

1. **Persist the complement.** The cap is already correct; Variant C prices it
   and then lets requoting and the fail-closed queue branch remove it. Keep one
   complement resting at the basis-derived price and stop re-deriving it every
   tick. *(A, L5-supported, largest measured term.)*
2. **Replace `extend_requires_balance` with a basis test.** Permit same-side
   extension when it *lowers* aggregate economic basis, refuse it when it
   raises. Variant C's blanket refusal bought the tail bound and paid for it
   with B. *(B, deliberate adaptation of L5-supported EXTEND.)*
3. **Instrument cadence; implement nothing for latency.** Record native-cadence
   full-depth frames in the forward shadow and measure C before spending
   anything on it.

Explicitly **not** in V1: no new threshold, no θ change, no residual backstop, no
clip increase, no capital increase, no concurrency.

## 8. The deliverable answer

**Can an evidence-supported L5-adapted engine plausibly produce ≥$5/window under
our executable conditions? On this evidence, no.**

- At the authorised $81 envelope (≈q10–q30): ceiling **+$2.00 to +$3.11/window**, both optimistic.
- At q50, consuming the whole $250 account as peak exposure: **+$4.14/window**, still short, and resting on fill assumptions under review.
- $5/window needs ≈q67 and ≈$330 peak — **more capital than exists in the account**, and justified only after the edge is shown to scale, which today it does not.

**The binding constraint is inventory decisions.** Fix those and edge per dollar
may become positive at size — at which point capital becomes the next real
constraint and the question can be reopened with evidence. Until then, more
capital makes this worse, measurably, at every clip above q10.

The honest recommendation: build V1 for mechanisms A and B, validate on forward
windows, and judge it against **whether edge per dollar is positive and stable as
clip rises** — not against $5/window, which this architecture cannot reach at
our capital.
