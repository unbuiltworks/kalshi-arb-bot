# P15 — Persistence-isolation experiment (PROPOSAL ONLY)

Tests whether the **action-budget architecture**, not the controller decision
rule, causes complement starvation. Nothing here is implemented or authorized.
Probe B is untouched.

## Preserved exactly — the decision rule does not move

q=1 · same controller · CAP4 · entry band 840:880 · quote band [60,880] ·
maker-only, never crossing · `max_working_orders` 2 · `max_committed_usd`
$4.00 · `max_residual_usd` $4.00 · pair economics ≤0.96 unchanged.

**Only the budgeting architecture changes.**

## Why splitting the budget does not add money at risk

Today one 12-slot ledger funds both `place()` and `cancel()` (ws_continuous
:1765, :1850). That conflates two different things: **exposure-increasing**
actions and **quote-maintenance** actions. A cancel cannot create exposure —
it can only remove it — yet it competes for the same slots as a place.

Economic exposure is already bounded independently, and none of those bounds
is an action count: CAP4 (max 4 fills), `max_committed_usd` $4.00,
`max_residual_usd`, `max_yes`/`max_no`, `max_working_orders` 2. **Raising the
action ceilings therefore raises venue interaction, not dollars.** Worst case
per window stays **$4.00**, identical to Probe A.

What it does raise, stated plainly: transport/rate-limit surface, and the
number of chances to fill — the latter still capped at CAP4 and $4.00.

## The five independent ceilings

| # | ceiling | basis |
|---|---|---|
| 1 | filled/committed economic exposure | **UNCHANGED** — CAP4, $4.00 committed, $4.00 residual |
| 2 | PLACE attempts | derived below |
| 3 | CANCEL / replace maintenance | derived below |
| 4 | total transport mutations | sum of 2 + 3; `CLEANUP_CANCEL` stays separately bounded |
| 5 | wall-clock participation | derived below |

## Minimum maintenance budget, from observed cadence

Observed refresh interval: **40.0 s per leg** (pooled over Probe A's 12 gaps).
Per leg a horizon *H* needs 1 initial place + `r = H//40` replacements; each
replacement is cancel + place. Two legs. A fill frees a leg without a cancel,
so CANCEL is an **upper** bound. Terminal withdrawal is already exempt.

| horizon | refreshes/leg | PLACE | CANCEL | TOTAL |
|---|---|---|---|---|
| 180 s | 4 | 10 | 8 | **18** |
| 300 s | 7 | 16 | 14 | **30** |
| 360 s | 9 | 20 | 18 | **38** |
| 600 s | 15 | 32 | 30 | **62** |
| 820 s (TTE=60) | 20 | 42 | 40 | **82** |

Probe A actual: W01 **11** actions / 157 s, W02 **12** / 122 s.

## Against L5's first-complement latency CDF

Measured from the window's first fill, canonical history:

| horizon | 30 s | 60 s | 90 s | 120 s | 180 s | 300 s | 600 s | 820 s | never |
|---|---|---|---|---|---|---|---|---|---|
| **BTC15m** (n=5,503) | 0.547 | 0.752 | 0.856 | 0.911 | 0.962 | **0.9867** | 0.9922 | 0.9924 | 0.0076 |
| BTC5m (n=8,089) | 0.167 | 0.313 | 0.426 | 0.508 | 0.616 | **0.6801** | 0.6801 | 0.6801 | 0.3199 |

**300 s captures 98.67% of BTC15m complement opportunities.** Going to the
full band buys **+0.57 pp** for 2.7× the actions. BTC5m saturates completely
by 300 s — nothing arrives after it.

This is the decisive sizing input: the full band is not worth buying.

## Replay of Probe A under each horizon

Counterfactual execution is asserted **only** where the tape supports it. No
fill is assumed merely because a quote existed.

### W01 `…0445-UP` — no supportable fill at any horizon

Non-our prints: 04:41:10 (t+632.8 s, 55.58 @ UP 0.83) and 04:41:57
(t+679.8 s, 5 @ UP 0.89). Both fall outside 180/300/360/600 s and inside the
full band.

Our YES bids were 0.44–0.67 — a trade at UP 0.83 is **above** all of them, so
no YES fill is supportable even at 820 s.

The 0.83 print equals NO 0.17, and we bid NO 0.19 at one point. Whether a
counterparty transacting NO at 0.17 was *selling* (and should have hit our
0.19 first) or *buying* depends on aggressor semantics in the printed price
space — precisely the question whose decoder gate **FAILED**. Recorded as
**AMBIGUOUS and not counted**.

### W02 `…0500-UP` — the single opportunity, and a correction

Prints: 04:50:28 (t+**297.9 s**, sell 277 @ 0.54 and 10 @ 0.55) and 04:55:50
(t+619.9 s, buy 394 @ 0.62, 10 @ 0.52).

| horizon | 0.54 print covered? | margin |
|---|---|---|
| 180 s | **no** | −117.9 s |
| 300 s | yes | **+2.1 s** |
| 360 s | yes | +62.1 s |
| 600 s | yes | +302.1 s |
| 820 s | yes | +522.1 s |

**A correction to the framing.** Under a maintenance architecture the order
would *not* still be "a 0.54 order" — it would have been repriced roughly four
more times by t+298. The answerable question is: *would a YES quote have been
resting at all, and at ≥0.54?*

- **Resting at all:** yes, for any horizon ≥300 s. That part is architectural.
- **At ≥0.54:** **unknowable.** The controller's desired price depends on the
  book, and no book exists for this window. The observed path was monotone
  rising — 0.26 → 0.41 → 0.42 → 0.54 — so ≥0.54 at t+298 is plausible if the
  trend held. A sell at 0.54 fills any bid ≥0.54, and 277 lots against our
  1 lot means size is not the constraint.

**Fill is PLAUSIBLE, not certain** — for two independent reasons: queue
position is unavailable, *and* the maintained price path is unobservable.

**300 s clears it by 2.1 seconds.** That is far too marginal to design
against, which is the argument for 360 s rather than 300 s.

## Proposed smallest bounded live persistence experiment

**Not authorized. Not implemented. Presented for decision only.**

| parameter | value | basis |
|---|---|---|
| family | BTC15m, one market at a time | comparability with Probe A |
| windows | **2** | exactly Probe A's count, like-for-like |
| wall-clock participation | **360 s** from first action | 300 s = 98.67% of the L5 CDF, +60 s for one refresh interval of margin; 300 s would have cleared W02 by 2.1 s |
| PLACE ceiling | **20** | 2 legs × (1 + 9 refreshes) |
| CANCEL ceiling | **18** | 2 legs × 9 refreshes |
| total transport mutations | **38** | 20 + 18; `CLEANUP_CANCEL` unchanged and still separate |
| economic exposure | **unchanged** — CAP4, $4.00 committed, $4.00 residual, q=1 | not a lever in this experiment |
| structural ceiling | **$8.00** = 2 × $4.00 | **identical to Probe A** |
| stops | $10 economic-loss (now with the terminal sweep) and $5 drawdown, unchanged | |
| authorization | a **new** id and fresh expiry — the latch is one authorization, one cohort | |

### Pre-registered endpoints

- **Primary:** did any window acquire an opposite-side fill? Probe A: 0 of 2.
- **Secondary:** time-to-first-complement against the L5 CDF above; actions
  consumed against each of the three ceilings; and **which ceiling bound
  first** — wall-clock or actions.
- **Design property worth measuring:** the ceilings are matched to the
  *observed* 40 s cadence. If the controller refreshes faster — say 25 s —
  360 s would need 58 actions and the action ceiling would bind at ~228 s.
  Which one binds is itself a result.

### Falsification, and its honest power

If 2 windows at 360 s still yield 0 complements, the action-budget hypothesis
is **not supported** and venue flow becomes the leading explanation.

**Power is weak and must not be overstated.** Under L5-like flow,
P(0 complements in 2 windows at 300 s) = (1 − 0.9867)² ≈ **0.018%** — but the
Gemini base rate is exactly what is unknown, so that figure bounds the L5
comparison, not this test. Two windows cannot separate a 50% per-window
complement rate from a 10% one. Four windows ($16.00) would give materially
more power at twice the money; that trade-off is the owner's.

### What this experiment cannot settle

Flow. At a median of 4 prints and $116.69 per BTC15m window, persistence
changes how long we are present for a scarce event — it does not create one.
P14's finding stands: our 122–157 s lifetimes already spanned past L5's p90
of 109 s, so budget alone does not explain 0 complements in 2 windows. This
experiment isolates the budget term; it does not test the flow term.

## Not proposed here

Candidates **C** (imbalance-triggered repricing) and **D** (bounded >$1 pair
tolerance) remain recorded and unimplemented. P15 deliberately changes neither
placement nor pair economics, so that a positive result is attributable to
persistence alone.
