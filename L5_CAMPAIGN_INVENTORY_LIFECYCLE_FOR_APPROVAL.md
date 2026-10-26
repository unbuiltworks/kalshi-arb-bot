# Bounded live campaign FOR APPROVAL — the inventory lifecycle

**Not authorized. Nothing in this document is armed, deployed or submitted.**
`armed=false`, `max_live_orders=1`, `max_pair_cost=0.96`, every risk limit at
its deployed value, `L5_TS_COMPLETION_CROSS` unset.

## 1. What this campaign tests, and what it does not

It does **not** test whether PMUS orders fill. That is demonstrated: 14
venue-confirmed fills across 7 windows, each with a `venue_order_id`.

It tests the one thing never tested with money: **whether a residual can be
exited economically inside the window.** Specifically, whether the fix in
`32d9e1c9` turns a strand into a bounded loss.

The defect it validates, measured on 1945z's own venue books:

| phase | pair own cost | best executable exit |
|---|---|---|
| MAKER [0,600) | 0.99–1.38 | **−$0.0144** |
| COMPLETION [600,720) — the only band the engine could cross | 1.33–1.41 | **−$0.5348** |
| LATE [720,900) | 1.37–1.43 | −$0.5872 |

`completion_decision` would have **accepted** the 204 s instant on its own
economics; it was never asked, because `completion_phase` returns MAKER until
600 s. The fix yields the MAKER bail when `still_fillable_order_count() == 0`
— i.e. only when no quote of ours can still fill, so there is no maker edge to
protect. All 121 residual samples of that window carry `order_resting: false`.

### Primary success criterion — not profit

1. At least one real residual reaches a completion decision with
   `evaluated_early_no_quote_resting: true`, in the MAKER phase.
2. That decision either crosses and settles, or refuses with a named reason.
3. Every order, cancellation, fill and settlement reconciles against the venue.
4. `realized_net_usd` is reported from settlement only.

**A campaign that strands and exits at a small bounded loss is a SUCCESS.** It
would be the first evidence that the lifecycle can be controlled. A campaign
that returns a profit on 4 windows proves nothing either way at this n.

### The question the campaign actually answers

Whether residual expectancy is negative. Under the market's own implied
probability an exit is EV-negative by about the taker fee — at 204 s the exit
was −$0.0144 against a hold EV of **+$0.0156**. It is a worst-case control,
not alpha. Exiting is genuinely profitable only if our fills are adversely
selected (adverse rate at entry 77.5% ours vs 30.8% L5's, which is a statistic
about fills and not a measured residual expectancy). This campaign measures it.

## 2. Capital ceiling

Observed on deployment `482cb9e3` in `L5_LIVE_GATES` / `L5_LIVE_PREFLIGHT`,
2026-10-26T03:31Z. **No value below is raised.**

| | |
|---|---|
| `max_cash_deployed_usd` | **$5.00**, ACCOUNT-WIDE |
| basis | filled-and-unreleased + **every still-fillable resting order at full worst case** + the proposed order |
| `cash_policy` | `NO_RECYCLING` — settled cash is not reused in the run |
| preflight worst case | **$5.1738** = $5.00 committed + $0.1738 max taker fee |
| `max_worst_case_deficit_usd` | $2.00 |
| `max_usd_per_market` | $20 (the $5 cash gate binds first) |
| campaign budget | $29.30, remaining $29.30 |

Verified in code against the deployed commit: `pending_obligation` charges a
resting long `qty*price` and a resting short `qty` (the whole $1/share);
`outstanding_for_gates()` is account-wide and prices an unreadable price at
worst case and an unreadable side as a short.

## 3. Maximum loss

| | |
|---|---|
| `max_l5_daily_loss_usd` | $20 |
| `max_l5_cumulative_loss_usd` | $20 |
| `max_cumulative_loss_usd` | $20 |
| `max_unmatched_contracts` | 5 |
| `max_abs_residual` | 10 |
| halt | independent watchdog latch, `L5_HALT_URL`, 20 s sweeps, `cancel_enabled: true` |
| realistic worst case, 4 windows | ~**$3.20** (4 x ~$0.80 full strand) |
| hard ceiling | $5.00 cash / $5.1738 worst case, then the $20 gates |

### Disclosure: deploying the loss-gate fix changes effective exposure

In the **deployed** commit the daily and cumulative ceilings read the *same*
post-baseline cumulative figure, so the ceiling named DAILY never resets. The
branch to be deployed separates them, which is correct and is what the owner
approved ($20 as a **daily** limit) — but it means the daily budget genuinely
renews where today it does not. **I am not raising a limit; deploying correct
semantics nonetheless permits more loss across multiple days than the current
bug does.** If that is unwanted, set `L5_MAX_L5_DAILY_LOSS_USD` below $20 for
this campaign.

## 4. Market and window scope

| | |
|---|---|
| series | **BTC15m only** (`cpc-btc-updown-15m-*`), venue-verified at preflight |
| concurrency | `max_concurrent_markets=1`, one market at a time |
| windows | **4** |
| why 4 | observed strand rate 4 of 7; P(at least one strand in 4) = 1 − 0.43⁴ ≈ **97%**, so 4 windows very probably produce the residual the campaign exists to test |
| entry | first leg while `tte >= 300` s, entry cutoff 600 s, `min_first_leg_own_price` 0.20 |
| clip | `opening_leg_max_contracts` 3, ~$0.80 notional |

## 5. Order limits

| | |
|---|---|
| `max_live_orders` | **1 — unchanged.** Not raised for this campaign |
| per-order TTL | 120 s from submit |
| complete cutoff | 720 s | 
| cancel margin | 180 s before window end; market-scoped deadman |
| `max_submissions` | 21, class budgets OPEN 6 / COMPLETE 9 / REPLACE 12 |
| completion reserve | 2 submissions per obligation; a risk-reducing order is never refused by the flat backstop |
| cancellation | `cancel_exact` by venue order id, terminal-proven before any replacement |

`max_live_orders=4` is **deliberately excluded.** It is blocked on the
daily/cumulative deploy, its fee-rounding tail is bounded by the order count
by the fee model's own admission, and the exit fix does not need it.

## 6. Expiration

| | |
|---|---|
| `L5_LIVE_AUTH_NOT_AFTER` | **3 hours** from arming, hard wall clock |
| windows | 4, whichever comes first |
| auto-expiry | the claim's own venue-bounded expiry already cancels on timeout |
| no auto-relaunch | a money-moving session must not relaunch itself after crash or completion |

## 7. Deployment and preflight verification

**The deploy is a 47-commit cutover, and that is the main risk here.**
Production runs `497b1152`; the branch head is 47 commits ahead and none is
deployed, because the service's only build `watchPatterns` entry is
`deploy/l5_pmus_live/DEPLOY_TRIGGER` and nothing has touched it. Those commits
include `src/l5_live_validation_gates.py` and `src/l5_live_execution.py`.
Touching that file ships all of it at once.

Required before arming, in order:

1. Review the validation-gate and execution deltas `497b1152..HEAD`, or cut a
   narrower branch.
2. Deploy and confirm a **fresh boot**. A variable write does not re-exec a
   running process — `497b1152` is the record of exactly that error.
3. Confirm every boot line: `L5_LIVE_START`, `L5_AUTHORIZATION_PREFLIGHT`,
   `L5_BUDGET_SELFCHECK`, `L5_EFFECTIVE_RUNTIME_CONFIG`,
   `L5_RESIDUAL_SIDE_SELFCHECK`, `L5_MANDATE_STORE_PROBE`,
   `PMUS_FILL_HORIZON_PREFLIGHT`, and `L5_INGEST_CURSOR` (`resumed`).
4. Read the effective ceilings from `L5_EFFECTIVE_RUNTIME_CONFIG` in the
   container, not from variable names — several approved figures are code
   constants with no variable at all.
5. Confirm the watchdog: halt latch CLEAR, `cancel_enabled: true`,
   `read_error: null`, `open_orders 0`.
6. `L5_LIVE_PREFLIGHT` PASSED on the target market, `no_unexpected_open_orders
   0`, `executor_starts_disarmed`.
7. Never restart mid-window: entry is confined to roughly the first 300 s and a
   restart past that silently spends the window.

### The one new permission this campaign requires

`L5_TS_COMPLETION_CROSS=YES` — without it the fix is evaluate-only and logs
proposals without placing anything. This is the switch that lets a taker
completion reach the venue, and it is the substance of what is being approved.

## 8. Not requested

No change to `max_live_orders`, `max_pair_cost` (0.96 stays), capital, loss
ceilings, concurrency, market scope, or autonomy. No taker-by-default: crossing
remains confined to `completion_decision`'s strict worse-outcome test. No new
service, database or strategy branch. No indefinite running — 4 windows and 3
hours.

## 9. Accounting

Status ladder enforced: `submitted -> filled -> settled -> realized`. Only
venue-confirmed settlement lines enter `realized_net_usd`. Rebates excluded
until credited and source-accounted (`UNVERIFIED_NO_VENUE_CREDIT_RECORD`).
Provisional windows counted, never netted. A shadow decision is never a trade.
