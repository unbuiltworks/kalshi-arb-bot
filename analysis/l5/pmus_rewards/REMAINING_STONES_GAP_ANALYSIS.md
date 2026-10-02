# Gap analysis: what this program has not examined

Scope as set: **portfolio-level inventory management, reservation-price resting,
state-dependent sizing.** The late-favorite / opposite-longshot lane is handled
separately and is deliberately not duplicated here.

This is a gap inventory, not a proposal to implement. Each item states what has
actually been tested so far, what has not, and what evidence already in hand bears
on it.

## 1. Portfolio-level inventory management

**What has been tested.** Nothing at portfolio level. Every result in this program
treats each first-leg lot as an independent unit: PAIR_OR_UNWIND prices lots one at a
time, the resting arm places one order per side per market, and the frozen baseline
exits lot by lot. The live progression was explicitly **one market at a time**.

**What the data already says is missing.** The window characterization makes the gap
concrete: at c.97, 1745z reached **190 concurrent open lots** against 1730z's **74**,
with forced-exit share **38.9% vs 17.7%**. Those are portfolio states, and nothing in
the current machinery is aware of them. A lot opened as the 190th is in a completely
different risk position from the 5th, and the baseline treats them identically.

**Specific untested questions.**
- Is the forced-exit rate a function of *concurrent* inventory rather than of the
  individual lot's entry? The 2.2× difference in forced share alongside a 2.6×
  difference in peak concurrency is suggestive and entirely unexamined.
- Does a cap on concurrent open lots dominate per-lot entry filtering? The entry-depth
  diagnostic already showed depth is not the lever and MAE runs 3–4× MFE at every
  depth; a concurrency bound is a different control surface that has never been tried.
- Cross-market netting is untouched: residual YES exposure in one window may offset
  another's, and CLAUDE.md bounds residual directional exposure without any mechanism
  measuring it across markets.
- Capital-hours per lot are computed but never used as an allocation constraint.

## 2. Reservation-price resting

**What has been tested.** Only two placement rules, both mechanical: the frozen arm's
touch-following peg, and the fixed-price resting arm (place once, never reprice). The
entry-depth diagnostic tested joining one and two ticks deeper and requiring the
market to come to us.

**What has not.** Any notion of a price we are *willing* to be filled at, derived from
inventory and expected adverse selection rather than from where the touch happens to
be. Every arm so far quotes **at the touch** and differs only in whether it chases.

**Specific untested questions.**
- A reservation price that widens with accumulated inventory — the standard
  market-making response — has never been implemented or measured, despite the
  inventory data being present.
- Quoting away from the touch and letting the market come in was tested only as a
  fixed depth, not as a function of state. MAE 3–4× MFE at every fixed depth says a
  *constant* offset fails; it says nothing about a state-dependent one.
- The pair-cost ceiling (0.99/0.98/0.97/0.95) is applied to exits only. There is no
  entry-side reservation condition at all: entries happen whenever the touch is
  consumed, regardless of what pair economics are then reachable.
- Asymmetric two-sided quoting — different offsets per side given current residual —
  is unexamined. Both sides are always quoted identically.

## 3. State-dependent sizing

**What has been tested.** Fixed q only: q1 for the Kalshi gate, q=25 throughout the
resting arms, with q∈{5,25,100} compared in the original PAIR_OR_UNWIND sweep. Size
has never varied *within* a window.

**What has not.** Any size that responds to state. CLAUDE.md's own promotion order is
**PATIENCE → FREQUENCY → SIZE → CONCURRENCY**, and the program has effectively worked
on patience and frequency while holding size constant, which is correct sequencing —
but it means sizing is genuinely unexplored rather than ruled out.

**Specific untested questions.**
- Size as a function of concurrent inventory, which is the natural pairing with item 1.
- Size as a function of time-to-expiry. The fill-time distribution differs sharply
  between the two windows ([174, 84, 29, 138] vs [90, 217, 96, 62] by quartile) and
  the baseline's forced exits are a deadline artifact, so expiry proximity plainly
  matters to outcomes while size ignores it.
- Whether the trade-flow signal now in hand is better used to **reduce size** than to
  cancel. Cancellation is binary and loses the qualifying fills; scaling down keeps
  optionality. This is the most direct extension of the current result and has not
  been looked at.
- Reward-program interaction: PMUS scoring is size-weighted at the touch, so sizing and
  incentive capture are coupled, and no analysis has treated them jointly.

## What this analysis does NOT claim

None of these is evidence of opportunity. The program's record is that the two most
promising prior leads — queue depletion at −3.73 sigma, and A420/A540 at +0.22
net/leg — both evaporated when the data improved. The honest status of every item
above is *unexamined*, not *promising*.

The binding constraint remains the same one: **pristine windows**. There are two.
Every question here needs a sample that does not yet exist, and the current priority
is accumulating it while scoring the frozen signal prospectively.
