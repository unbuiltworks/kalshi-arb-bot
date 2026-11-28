# Stage B2 — repeatability campaign proposal (NOT ARMED, NOT AUTHORIZED)

Purpose: establish whether **matched profits consistently exceed residual
losses across real settled windows**. Nothing here is armed. No order has been
submitted. This requires fresh owner authorization.

Engine: **unchanged** — deployment `f51840b8`, commit `ff00d287` (carries the
`69b19625` DOWN-completion fix), service `l5-pmus-live`. No new service, no new
branch, no code change, no crossing, no capital increase.

## The question the campaign must answer, and the statistic that answers it

Total realized P&L **cannot** be the decision statistic. Residual P&L has ~0
expectation and a ±$1 swing at a ~33% strand rate, giving ~$0.58 of noise per
window against a ~$0.08 signal: deciding on total realized would need
**~185 windows**. That is not a campaign, it is a different programme.

The decision statistic is therefore the **matched component**, which is
outcome-independent because a completed pair redeems exactly $1.00:

> **edge per pair = $1.00 − pair_cost**, one observation per settled pair.

Residual P&L is recorded, reported and bounded — but it does not enter the
decision.

## Smallest useful window count: 8

Pre-registered sizing from Stage B1 (mean +$0.0125/contract, stdev 0.0282,
2.67 pairs per traded window):

| windows | pairs | power at α=0.05 one-sided |
|---|---|---|
| 4 | ~11 | ~45% |
| 6 | ~16 | ~55% |
| **8** | **~21** | **~65%** |
| 12 | ~32 | ~81% |

**8 windows** = 2 hours = ~21 pairs at ~65% power. It is chosen because it is
the **largest the existing approval generator already permits**
(`MAX_WINDOWS = 8`, `MAX_CAMPAIGN_S = 4h`) — so it needs **no ceiling change**
— and the smallest that can reach significance at the observed effect size.

**Stated plainly: ~65% power means a null result at 8 windows would NOT
establish absence of edge.** Reaching 80% needs ~12 windows / ~32 pairs, above
the current ceiling. If Stage B2 comes back null-but-positive, the honest next
step is a second 8-window campaign pooled with the first, not a louder claim
from the same data.

## Exact limits — every dollar bound UNCHANGED from the authorized Stage B1

| limit | value | change |
|---|---|---|
| aggregate exposure | **$20** | unchanged |
| per-market worst-case deficit | **$1.50** | unchanged |
| daily settled-loss halt | **$5** | unchanged |
| max live orders | **1** (existing documented pairing exceptions only) | unchanged |
| execution | **maker-only / post-only** | unchanged |
| taker crossing | **disabled** (`L5_TS_COMPLETION_CROSS` untouched) | unchanged |
| opening size | `ts_opening_leg_max_contracts` **3** | unchanged |
| pair ceiling | **0.96** | unchanged |
| max cash deployed | **$12.00** | unchanged |
| round allocations / acc rounds | **4 / 4** | unchanged |
| cutoffs, cancel margin, TTL | 600 / 720 / 300 / 120 s | unchanged |
| all watchdog, reconciliation, cancellation, expiry, halt protections | retained | unchanged |

**The one thing that must change, and it is not capital.** Stage B1 authorized
4 windows but funded ~3. For 8 windows the submission-class budgets must be
sized to 8 windows:

| class | Stage B1 | observed burn/window | **Stage B2 request** |
|---|---|---|---|
| OPEN | 12 | 4.00 (6, 4, 2) | **32** |
| COMPLETE | 12 | 3.33 (3, 4, 3) | **32** |
| REPLACE | 6 | 0.33 (0, 0, 1) | **8** |
| flat backstop `MAX_SUBMISSIONS` | 27 | OPEN+REPLACE 4.33/win | **56** (see note) |

COMPLETE is held at ≥ OPEN deliberately, so the 2-per-obligation completion
reserve cannot lock out opening the way it did in 0300z.

**Note on the flat backstop.** `max_total_submissions` is set to **56** —
below the sum of the three classes (72), keeping the generator's convention
that OPEN 32 + COMPLETE 32 cannot both run to their ceiling.

It is safe at that value because the backstop **does not bind a risk-reducing
order**: the call site guards it with `total_backstop_binds(reducing)`, which
returns `not reducing`, so a COMPLETE can never be refused by it and cannot
strand unmatched inventory. The backstop therefore bounds only OPEN + REPLACE
= at most 40, against ~35 expected across 8 windows (observed OPEN+REPLACE was
13 over 3 windows). It cannot silence a window the way the exhausted OPEN
class silenced 0300z.

**Why this is not a risk increase.** Submission counts are activity budgets,
not money. Worst-case loss is bounded by the **$5 daily settled-loss halt**,
which is unchanged and which stops the campaign automatically. At the observed
strand rate (1 per 3 traded windows, worst case −$0.877 each), 8 windows
implies ~2.7 strands and a realistic worst case near **−$2.4**, inside the
halt. Per-window and per-market risk are untouched: same deficit cap, same
exposure cap, same one live order, same opening size.

## Expiry and automatic disarm

| control | setting |
|---|---|
| windows | exactly 8 consecutive BTC15m windows: **1300z 1315z 1330z 1345z 1400z 1415z 1430z 1445z** (2026-10-31 UTC) |
| `starts_utc` | set, so `WINDOW_BEFORE_AUTHORIZED_START` refuses any earlier window (proven on 0145z) |
| `expires_utc` | **2026-10-31T15:15:00Z** — first window + 2h15m (8 windows + one window of settlement slack); span 2.25h vs the 4h ceiling |
| `starts_utc` value | **2026-10-31T13:00:00Z** |
| campaign age bound | `MAX_CAMPAIGN_S = 4h` — unchanged, not approached |
| automatic expiry | `approval_gate()` refuses past `expires_utc` (proven: 03:15:10 `APPROVAL_NOT_VALID / approval_expired` refused 0315z) |
| automatic disarm | `window_budget` 8; the 9th window cannot arm |
| end state | `armed false`, `owner_arm_token false`, `PMUS_Q1_ARM` cleared, shadow lock restored |
| no auto-relaunch | money-moving sessions fail closed and do not relaunch after crash or completion |

## Stopping rules

**Economic / safety — automatic, already in the engine, no new code:**

1. `utc_day_realized_usd` reaches **−$5** → halt, campaign over.
2. Approval expires → every window refused.
3. 8 windows spent → no further arming.
4. Any reconciliation failure or ambiguous POST → halt re-entry pending
   authoritative reconciliation.

**Statistical — pre-registered now, so the verdict is not chosen after the
fact.** On settled pairs only, one-sided α = 0.05:

- **GO** if the one-sided 95% lower bound on mean pair edge is **> 0** —
  i.e. `mean > 1.645 × s / √n`. At n≈21, s≈0.028 that is
  **mean > +$0.0101/contract**, roughly **cumulative matched P&L > +$0.43**.
- **NO-GO (inconclusive, not refuted)** if the lower bound stays ≤ 0 after 8
  windows. Report it as inconclusive at ~65% power. Do not re-run the same
  campaign hoping for a different draw; pool with Stage B1 and state the
  pooled n and power.
- **INVESTIGATE, DO NOT AUTO-STOP, on a negative run.** *(Owner decision,
  2026-10-31: the proposed five-window negative-P&L automatic stop is
  replaced.)* If cumulative matched P&L is negative mid-campaign, that is a
  signal to investigate while the campaign continues — matched P&L being
  outcome-independent makes a negative run worth diagnosing, but a negative
  draw is not by itself a defect, and halting on one would discard the sample
  the campaign exists to collect. **Halt only on a confirmed defect or an
  existing risk trigger** (the four automatic rules above). A suspected but
  unconfirmed defect is reported, not acted on by halting.
- **Robustness condition, reported alongside:** the verdict must survive
  dropping the single best pair. Stage B1 fails this (80% of its edge was one
  pair of eight), and a Stage B2 verdict that depends on one pair is not
  repeatability.

## What is also measured, but does not decide

- residual frequency, size, worst case and settled outcome per window;
- strand attribution: how often a *second* accumulation round's first leg
  strands (the 0245z mechanism — the one structural avoidable loss found);
- whether Route B `book_completion` ever places, which would be the first live
  evidence for `69b19625`;
- whether `max_live_orders 1` serialises without preventing completion
  (0230z says it does not);
- maker rebates: recorded, **counted as $0** until actually credited.

## Preflight before arming (unchanged from Stage B1, all must pass)

serving SHA and DOWN-completion fix · authoritative cash · venue positions and
open orders · watchdog health (`l5-halt-watchdog`, `cancel_enabled true`) ·
exact UTC window schedule · no conflicting authorization · fresh campaign
baseline. **If any preflight fails: do not submit; report the exact blocker.**

## Authorization required

| item | status |
|---|---|
| 8 windows (ceiling already 8) | **needs owner approval** |
| submission budgets OPEN 32 / COMPLETE 32 / REPLACE 8 / flat 72 | **needs owner approval** |
| every dollar limit, order limit, maker-only rule, crossing ban | unchanged — already authorized in form, but a new campaign needs a new nonce |
| new services / capital / crossing | **none requested** |

Additional infrastructure cost: **$0.** Same service, same deployment, same
commit.
