# SEPARATE HYPOTHESIS — patient directional maker (Brett's observed behavior)

**Preregistered before any fitting. No offsets, thresholds or horizons are
chosen yet. No P&L has been computed for this hypothesis. No live orders.**

This is kept **separate** from, and does not revive, the closed lanes:
`115de70b` (passive back-of-queue maker — closed on capacity) and
`d6f04be5` gate A (favorite-only entry, taker round-trip/exit, generic
favorite-flip inventory — all failed on measurable arms).

## THE HYPOTHESIS, STATED AS A MECHANISM

> Direction is chosen FIRST from BTC state; price is chosen SECOND and
> patiently. A limit rests below the contemporaneous ask and may fill later —
> and the fill is only worth having if the ORIGINAL directional thesis is
> still valid when it arrives.

This is **structurally different** from both failed lanes. The failed maker
test rested **at the touch** and took whatever filled; this rests at a price
chosen from estimated value and conditions the *value* of a fill on whether
the thesis survived to fill time. The observation that Brett's orders
sometimes rest before being picked up is treated as a **mechanism to test, not
as evidence of edge.**

## WHY THIS IS NOT ALREADY ANSWERED

The closed lanes measured that maker fills are **adversely selected** — a
resting buy fills precisely when price comes down through it. That finding is
exactly what makes this hypothesis worth separating rather than assuming: it
predicts that fills split into two populations, and the whole question is
their **ratio and relative economics**:

| | |
|---|---|
| **A. PATIENT GOOD FILL** | the market temporarily came to our limit, the BTC-state thesis still holds, and estimated value still exceeds the fill price |
| **B. ADVERSE-SELECTION FILL** | we filled because information moved against the original thesis, and the limit is no longer positive EV |
| **C. NO FILL** | the signal may have been right, but price never came to us |

**If every fill is B, the mechanism is the already-refuted one. If a material
share are A, it is a different mechanism.** That is the test.

## TWO SEPARATE STATES PRESERVED PER HYPOTHETICAL ORDER

Nothing is collapsed into one record; the point is to compare them.

**SIGNAL / SUBMISSION STATE:** timestamp · TTE / time into window · BTC price ·
strike · distance from strike · normalized distance · momentum and path
variables (1/5/15/30/60 s, acceleration, candle body/wicks, strike crossings,
time above/below strike, max excursion, realized vol) · Gemini bid/ask/depth ·
model probability · selected side · limit price · expected edge at that limit.

**FILL STATE:** fill timestamp · seconds resting · BTC price · strike distance ·
Gemini market state · queue state (`q_ahead`, through-price vs same-price
FIFO) · **whether the original signal still holds** · updated estimated
probability · edge at the actual fill · maker eligibility and rebate.

**Then settlement**, from the venue's own `resolution_side` and
`settlement_value`.

## FILL-DELAY BUCKETS (fixed now)

`0–1 s · 1–5 s · 5–10 s · 10–20 s · 20–30 s · 30–60 s · >60 s`

## COMPARISON POLICIES — the patient policy must beat all of these

1. **immediate taker** at the contemporaneous ask
2. **maker-at-touch** (the generic policy already refuted — included as the
   control that makes "patient" meaningful)
3. **patient maker limit** chosen from estimated value
4. **no trade**

## MEASUREMENTS

fill rate · time-to-fill distribution · settlement win rate · **entry
improvement versus the original ask** · rebate (defensible only) · net
P&L/fill · **net P&L per SIGNAL including unfilled signals** · adverse-
selection rate (share B) · capacity per window.

**Net per signal, not net per fill, is the headline.** Net per fill can be
made to look good by filling rarely; only per-signal accounting charges the
policy for the opportunities it missed.

## EXECUTION RULES — INHERITED, NOT RELAXED

- No touch = fill. No invented queue. No midpoint fills. No future knowledge.
- Queue model: the corrected event-level FIFO — a print strictly **through**
  our price fills regardless of queue; a print **at** our price fills only
  once observed traded volume exceeds the displayed queue we joined behind;
  cancellations do **not** advance us.
- **Do NOT cancel or reprice merely because the touch moved.** Queue priority
  is preserved unless the original signal becomes invalid or risk/TTE forces
  cancellation. (The earlier lane measured 102 reprices per fill from
  touch-chasing; that is the mistake this rule exists to prevent.)
- Fees/rebates: Decimal, ceil-per-fill fees, floor rebate, band $0.20–0.80
  enforced; theoretical rebates never counted as cash.
- The pair-cost identities remain enforced in `fi_core.py`.

## WHAT IS DELIBERATELY NOT DECIDED YET

The **limit offset**, the signal threshold, the "signal still holds" test and
the cancellation rule are **not chosen**. They will be specified once the
cohort is adequately powered, frozen with a SHA, and only then evaluated on a
genuinely prospective cohort. Choosing them now, against data we are about to
collect, is the failure mode this document exists to prevent.

## ORDER OF WORK

1. Collect to ~300 usable windows (below).
2. Answer the **conditional-calibration** question first, independently of
   P&L: for the same Gemini probability band, does settlement frequency
   differ materially by BTC state?
3. **Only if (2) shows separation**, specify and freeze the patient-maker rule
   — because a patient limit on a direction with no edge is just a cheaper way
   to lose.
4. Then evaluate prospectively, once.

**Step 3 is conditional on step 2 by design.** The patient-execution mechanism
improves *entry price*; it cannot manufacture directional edge. If BTC state
carries no conditional information, patience changes the price at which we are
wrong, not whether we are wrong.
