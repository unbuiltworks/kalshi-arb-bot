# Shadow breadth, first prospective read: the selector works, the measurement does not yet

OWNER: *"analyze the live shadow-breadth data prospectively... whether the new
selector is actually finding markets where frozen R5F would produce better
completion economics."*

Window: 23 cycles, 2026-09-10 21:35→21:57Z, deployment `7dfb4882`
(`502fdf27`). 0 errors. Live probe `a88dff63` untouched throughout.

---

## The headline is a zero, and it must not be read as a completion rate

| tier | selection cycles | distinct markets | virtual first fills | virtual EXTENDs | **≤$1 COMPLETEs** | residual-opening markets |
|---|---:|---:|---:|---:|---:|---:|
| T1_top8 | 143 | 15 | 4 | 13 | **0** | 7 |
| T2_9_16 | 15 | 8 | 1 | 1 | **0** | 1 |
| **total** | 158 | **23** | **5** | **14** | **0** | **8** |

**Risk-recycling ratio: returned $0, created > $0 → 0.0.**

That is the "poor ratio" case: on this window the engine manufactures
residuals and recycles nothing. **But the window cannot support that
conclusion**, and the reason is mechanical rather than economic.

### Why the zero is a measurement artifact, not a market fact

I checked whether a COMPLETE is even reachable in the shadow harness before
reporting the zero. It is — `decide_market` calls `inv.record()` on a
possible fill, so inventory accumulates and `plan_action` can emit
`complete` once `inv.residual != 0`. Not impossible by construction.

What actually gates it:

1. **`DECISION_MIN_GAP_S = 30s`.** A market yields one decision per 30s of
   observation time, so a market watched for 20 minutes yields ~12 actions
   at most (`MAX_ACTIONS`).
2. **Virtual fills are rare.** In a direct probe: **8 quoted actions, 1
   classed `possible`** — a 12.5% virtual fill rate, and `possible` is
   itself an upper bound with queue position unproven.
3. A completion needs a *possible fill first*, then a residual, then a
   further decision at ≥30s spacing that the model routes to `complete`.

So P(reaching a completion) in a ~20-minute observation window is small
*even if the policy completes perfectly well*. Twenty-three markets over
twenty-two minutes is nowhere near the exposure needed.

> **What I can state:** across 23 markets and 158 selection-cycles the frozen
> policy produced 5 virtual first fills, 14 extends and no completions.
> **What I cannot state:** a completion rate, a time-to-completion, a
> pair-cost-at-completion distribution, or whether flow/spread/movement
> predicts completion. All four need completions to exist. Reporting "0%
> completion rate after first-leg fill" would be a real-sounding number
> extracted from a window too short to contain the event.

---

## What the window does establish

### The selector is picking the right shape of market

Every eligible market in the window was an **NFL game moneyline or spread**
— settlement horizons in hours, not the four-day cricket fixture that took
the live slot. The rank order rotated sensibly as the Thursday-night game
ran: `aec-nfl-sf-lar-2026-09-10` held the top slot at **$133k–276k/min**,
with the Sunday slate (`chi-car`, `bal-ind`, `den-kc`, `gb-min`, `dal-nyg`)
filling in at $600–6,500/min.

### Pair-cost distribution: uniform, and thinner than the live cohort

| maker pair cost | share of eligible rows |
|---|---|
| 0.990 | spreads (`asc-…-pos-6pt5`, `-neg-2pt5`) |
| 0.995 | every moneyline |

**A 0.5–1.0 cent margin, on essentially every market, all window.** Compare
the live cohort's realised completions: 0.9477 and 0.98 on tennis/esports.
The NFL markets the new selector favours are *tighter* — better flow, less
margin per completed pair. That is a genuine trade-off the ranking does not
currently price, and it points at the economics rather than the plumbing.

### EXTENDs outnumber first fills 14 : 5

Same shape the live probe shows (`opening_budget_spent:6/6:extension_is_
completion_only_not_extend`). The policy acquires more readily than it
completes, in shadow as in life.

### Opportunity the old discovery path would have missed

The live campaign's universe is 2,037 rows and it worked ITF/Setka tennis,
CS2, Dota2 and county cricket — **zero NFL**. Every market in this window is
NFL. The discovery gap is real and this is a second, independent
demonstration of it.

---

## Two defects found, both mine, both fixed in this commit

**1. Cohorts were memory-only, and I erased them.** Pushing `502fdf27`
touched `pmus_sports_opportunity_rank.py` — a **watched path for the
shadow-breadth service** — which redeployed it at 21:32Z and silently reset
every prospective tally. The commit message for `502fdf27` also asserted
"NOT on any deploy watch path, so nothing restarted"; **that was wrong**. A
loop whose entire purpose is accumulating evidence across time cannot keep
that evidence where a routine push erases it. Cohorts and dedupe keys now
persist to disk and reload on boot, with tests covering restart, no
double-count on restart, a missing file, and an unwritable path.

**2. Aggregate tallies cannot answer the owner's questions.** Time-to-
completion and pair-cost-at-completion need per-market rows, not per-tier
counters. The cycle row now carries a `markets` array plus spread, movement
and touch depth on the top rows, so the cohort analysis becomes possible
from logs alone.

---

## What would make this measurement real

Not a policy change — **exposure**. The window needs to be long enough for
completions to occur at their natural rate:

- accumulate over **hours**, not 22 minutes, now that cohorts survive restarts;
- report the funnel per hour and per day rather than per cycle;
- only then compute completion rate, time-to-completion, and whether
  flow/spread/movement predict completion.

The one thing I would flag for the economics, separate from sample size:
**if NFL moneylines really do sit at 0.995 while the tennis/esports markets
completed at 0.9477–0.98, then flow-ranking alone may be selecting for
markets that are easier to enter and worse to complete.** That is a
hypothesis this window raises and cannot settle, and it is the first thing
the matured data should be pointed at.
