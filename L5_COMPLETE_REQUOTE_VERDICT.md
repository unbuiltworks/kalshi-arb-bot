# COMPLETE repricing live test — **FAIL**

**VERDICT: FAIL.** Not because the mechanism is wrong — the corpus says it is the right mechanism —
but because **every completion in its own evidence base is refused by our deployed 0.96 pair cap.**
Arming it today would cancel resting orders and replace them with nothing.

Nothing was armed, deployed, or changed. Production is shadow-only, `armed=false`.

## 1. Why it was disabled, and whether the concern still holds

**The stated reason:** `L5_COMPLETION_EXTENSION_GATE_v1` is "frozen and accumulating, and a second
change to the same surface would make neither attributable."

**Where that gate actually stands** (`analysis/l5/completion_extension_eval.json`, gate sha
`fd6e31ddfbc42b40`, frozen 2026-09-08 — 47 days ago):

| sample minimum | have | need |
|---|---|---|
| forward_hours_on_the_extension | **2.35** | 48 |
| sessions_where_the_extension_fired | **10** | 25 |
| settled_venue_confirmed_markets | **5** | 15 |

The extension fired in **10 of 497 sessions (2.01%)**, at ~0.235 forward hours per firing. Reaching
48 forward hours needs ~204 firings ≈ **~10,100 sessions ≈ ~106 days of continuous single-market
live running.**

**So the concern is dormant, not void.** While we are shadow-only nothing accrues to either gate,
so there is nothing to contaminate. The moment live resumes it becomes real again — and the A/B
design's own costing of it (*"its remaining ~20 firings move from about 5 forward hours to about
10"*) **understates it by roughly 10x**: 45.65 forward hours remain, not 5.

This no longer decides anything, because the reconciliation below rejects the proposal before
contamination is reachable.

## 2. Reconciliation with the deployed configuration — this is what fails it

The replay ran inside the **spring** session, whose ceilings are `SPRING_PAIR_TARGET = 0.98` and
`COMPLETION_PAIR_CEILING = 1.00`. The deployed L5 engine caps at **`maker_pair_ceiling` 0.96.**

All 15 priced counterfactual completions, scored against the deployed cap:

| side | our px | requote px | units | basis | pair | deployed 0.96 cap |
|---|---|---|---|---|---|---|
| NO | 0.51 | 0.52 | 5.2 | 0.48 | 1.00 | REFUSE |
| YES | 0.555 | 0.580 | 4.5 | 0.440 | 1.020 | REFUSE |
| NO | 0.58 | 0.59 | 5.5 | 0.41 | 1.00 | REFUSE |
| YES | 0.50 | 0.54 | 5 | 0.49 | 1.03 | REFUSE |
| NO | 0.53 | 0.56 | 0.02 | 0.45 | 1.01 | REFUSE |
| NO | 0.62 | 0.63 | 0.02 | 0.36 | 0.99 | REFUSE |
| NO | 0.48 | 0.51 | 4.90 | 0.51 | 1.02 | REFUSE |
| YES | 0.61 | 0.62 | 4.09 | 0.39 | 1.01 | REFUSE |
| NO | 0.60 | 0.72 | 5.07 | 0.36 | 1.08 | REFUSE |
| YES | 0.59 | 0.63 | 3.9 | 0.35 | **0.98** | REFUSE |
| YES | 0.59 | 0.62 | 1.11 | 0.57 | 1.19 | REFUSE |
| NO | 0.56 | 0.62 | 5.24 | 0.38 | 1.00 | REFUSE |
| YES | 0.55 | 0.57 | 0.31 | 0.44 | 1.01 | REFUSE |
| YES | 0.59 | 0.61 | 4.23 | 0.40 | 1.01 | REFUSE |
| YES | 0.50 | 0.66 | 5 | 0.49 | 1.15 | REFUSE |

**0 of 15 allowed. 0 of 54.09 units.** The cheapest pair in the entire set is **0.98**; the median
is 1.01. (My reproduction of the replay's locked-pair total, −$1.7128, matches it exactly, so this
reads the same quantity the bar scored.)

**And the failure mode is worse than inaction.** `check()` returns `touch_moved:{touch}` as a
**cancel** reason while the order rests. The resting complement is cancelled first; the replacement
at the touch is then refused by the 0.96 pair gate. Net effect: **we end the window with no
complement resting at all** — strictly worse than the static order we have now.

Other deployed constraints, for completeness:

| deployed constraint | compatible? |
|---|---|
| one outstanding order | **yes** — a reprice is cancel-and-replace, not an addition |
| maker-only / post-only | **yes** — `would_cross` is checked before `touch_moved` |
| exact-order cancellation | **yes** — exact-ID cancel exists; the problem is the refusal *after* it |
| $5 total exposure | **yes** — `pending_obligation()` counts every resting order at full worst case |
| $0.80 opening notional | **no, scale mismatch** — the replay's completions are 0.02–5.5 units (median ~4.5); ours are 1.11–1.63. ~3x smaller per completion |

## 3. Expected net economic improvement, every term

| term | value | note |
|---|---|---|
| **above-par completion cost** | **−$1.7128** total, −$0.0317/unit | 10 of 15 above par; median 1.01, worst 1.19 |
| **realistic queue movement** | **79.0% of samples move us into a DEEPER queue**, median **4.3x**, p90 46.2x | n=501 live venue books. Your caution was right: the touch rung holds a median 5,642 contracts against 1,588 at our cap-forced rung, and a replacement joins the **back** of it |
| **partial fills** | 2 of 15 rows have `fillable_units` 0.02 | effectively nothing; counted in the 15 |
| **venue fees** | **$0.00** | PMUS maker fee is zero (`pmus_fees._fee`, 20 tests). Neutral, not a cost |
| **maker rebates** | **≈$0.02 across all 15** | at our $0.80 clip, 10 of 12 distinct completion prices round to **$0.00** (raw 0.0011–0.0057, cent-rounded ROUND_HALF_EVEN). Only 0.51 and 0.52 pay a cent |
| **settlement / avoided stranding** | +$17.9281 claimed | charged at a cohort mean of −$0.331449/unit drawn from **four markets with one binary outcome each — effectively n=4** |
| **net under the DEPLOYED config** | **$0.00, then negative** | nothing executes; the cancelled-and-refused orders make it worse than static |

The replay says the decisive thing about itself, and it should be quoted rather than paraphrased:

> *"If `locked_pair_usd` is negative the change loses money on every pair it makes and is only
> defensible if you believe the stranding estimate."* … *"a residual unit bought at fair value has
> expected P&L near zero, not −$0.33 … **If residuals are fairly priced this change simply loses
> the locked amount**"* — which is **−$1.7128**.

So even in the spring configuration where it *can* execute, the entire +$16.22 is one n=4 estimate.
The trades themselves lose money.

## 4. Bounded live test configuration

**Not prepared.** A test whose mechanism cannot execute under the deployed cap would produce zero
treated completions and consume live risk budget to learn nothing. Preparing limits, expiry and
success criteria for it would be false precision.

## What would make this testable

Exactly one modification, and it is **not** a new mechanism — it is a constraint on the candidate
already identified: **make the reprice cap-respecting.** Re-post only at a price that still clears
the 0.96 pair cap, and hold the existing order when no such price exists, so a cancel never happens
without a legal replacement. That removes the cancel-then-refuse failure and the queue-priority
loss in one change, because it only ever moves when the move is both legal and toward a reachable
price.

It needs its own offline replay before anything else — the frozen bar `699cc8205fd23383` scored an
uncapped reprice and **may not be reused** for a capped one. I have not built it.

The alternative — raising the pair ceiling to 0.98 so the validated version can execute — is a
separate risk change I have not made and would not recommend on current evidence: I tested the
ceiling and returned FAIL, and verified by execution that above 0.99 it is inert while at
one-cent spreads it is worth exactly one tick.

---

# FINISHED EVALUATION — **FAIL**, and the blocker is not the flag

Three things were outstanding. All three are now closed, and one of them retracts my own proposal
from the previous turn.

## A. My "cap-respecting reprice" is withdrawn — it is vacuous

I proposed re-posting only at a price that still clears 0.96. Tested against the replay's own 15
rows:

| | |
|---|---|
| reprices that move **UPWARD** (pay more) | **15 of 15** |
| resting COMPLETE orders legal under the deployed 0.96 cap | **3 of 15** |
| rows with any price both **legal** and **closer to the touch** | **2 of 15** |

Every observed reprice pays more, because the trigger is the touch moving up. A cap that forbids
paying more therefore forbids the move itself. The two surviving rows allow 1–2 cents of travel.
**Withdrawn.**

The deeper fact: **12 of 15 of these COMPLETE orders would never have rested at all** under the
deployed cap — pair at our own resting price is already 0.98–1.16. The replay's population is
mostly unreachable for us before repricing is even considered.

## B. Does repricing improve fill probability enough? **The gain was never measured; all three costs were**

| term | measured? | value |
|---|---|---|
| **fill-probability gain** | **NO** | the replay measured `book_reached_requote_px` = **45 of 72 (62.5%)** — price **reachability**, not fill. Queue position is not published, so reached ≠ filled. The numerator of the question has no measurement anywhere in the evidence base |
| lost queue priority | yes | **79.0%** of moves enter a **deeper** queue, median **4.3x**, p90 46.2x — and at the **back** of it (n=501 live venue books) |
| above-par cost | yes | **−$1.7128**, −$0.0317/unit, 10 of 15 above par |
| additional adverse selection | yes, by construction | every reprice is **upward**: the touch moved up because our complement got more expensive, and the rule buys it immediately after. The trigger *is* an adverse-selection event |

**Answer: no.** An unmeasured gain against three measured costs.

## C. The economics against the deployed $5 envelope — the real blocker

Venue-confirmed openings (acquisition and realized are venue-confirmed; the completion column is a
**counterfactual**, not a shadow fill):

| window | px | units | basis | realized | lost 100% of basis | completion @0.99 |
|---|---|---|---|---|---|---|
| 1245z | 0.57 | 1.4 | 0.7980 | −0.2940 | no | +0.0140 |
| 1300z | 0.63 | 1.26 | 0.7938 | **−0.7938** | **YES** | +0.0126 |
| 1315z | 0.46 | 1.63 | 0.7498 | +0.0815 | no | +0.0163 |
| 1330z | 0.33 | 1.29 | 0.4257 | +0.2130 | no | +0.0129 |
| 1345z | 0.58 | 1.5 | 0.8700 | −0.7206 | no | +0.0150 |
| 1400z | 0.23 | 1.11 | 0.2553 | +0.0555 | no | +0.0111 |
| 1945z | 0.44 | 1.56 | 0.6864 | **−0.6864** | **YES** | +0.0156 |

- venue-confirmed acquisition **$4.5790**, venue-confirmed realized **−$2.1448 (−46.8%)**
- completing only the two full-basis losses: **+$1.5084 → portfolio −$0.6364**, still negative
- **PERFECT completion of all seven at pair 0.99: +$0.0975 total**

That last line is the ceiling of the entire mechanism at this envelope:

| | |
|---|---|
| per window | **+$0.0139** |
| x 96 windows/day | **+$1.34/day** |
| against the objective | **0.28% of $480/day** |

**9.75 units of inventory x $0.01 locked per pair is all there is.** A maker pair at a one-cent
spread earns a cent, the PMUS maker fee is zero, and the rebate rounds to $0.00 at these prices.
No version of the completion mechanism — repriced, capped, uncapped, perfectly executed — clears
one half of one percent of the objective at a $0.80 clip.

## THE EXACT BLOCKER

Not the flag. Not the 0.96 cap. **Clip size against a fixed per-pair margin.** The margin is set by
the book (a one-cent spread pays one cent); the clip is set by the $0.80 opening notional. Their
product is $0.0139/window, and the objective is 345x that.

## THE SMALLEST CHANGE THAT RESOLVES IT

Capital, not code — and two independent routes agree on the size:

| route | requirement |
|---|---|
| idealised $0.01/pair | 500 pairs/window → **~$495 gross outlay/window** |
| **L5's measured blended 1.107% of turnover** | **$43,360/day → ~$452/window** |
| L5's measured settlement edge alone, 0.185% | $259,459/day → $2,703/window |

The first two agree within ~10% and both say **~90–100x the deployed $5 envelope**. The third says
that without incentive income the objective is out of reach by another order of magnitude — which
is the same conclusion the economic decomposition reached from the other direction, and it is why
the reward line matters more than any completion rule.

(The $0.01/pair route is an idealisation and **overstates**: L5's median *final* VWAP pair cost is
0.9709, not every unit of turnover is a matched pair, and his measured settlement edge is 0.185%.
Route B is the authoritative one.)

## What I did not do

No flag armed, no code changed, no deployment, no risk limit touched, no new service, no branch.
Production remains shadow-only with `armed=false` and the Oct 25 campaign expired and not re-armed.
