# Four-window PMUS BTC15m live campaign — activation runbook

**Status: PREPARED, NOT AUTHORIZED.** Nothing in this file has been applied.
The executor is DISARMED and cannot submit.

## What is already in force (verified in-container, 2026-10-26 13:29:13Z)

Release `728d3bb2` on `l5-pmus-live`, deployment `47920949` SUCCESS. Values read
by the money path's own accessors, not from variable text:

| gate | value | source |
|---|---|---|
| `max_cash_deployed_usd` | **4.64** | `L5_LIVE_GATES` |
| `max_worst_case_deficit_usd` | **0.90** | `VG.parse_deficit_bound` |
| `max_l5_daily_loss_usd` | **5** | `L5_LIVE_GATES` |
| `max_l5_cumulative_loss_usd` | **20** | `L5_LIVE_GATES` |
| `max_live_orders` | **1** | `R.max_live_orders()` |
| `max_windows_env` | **4** | `AUTH.max_windows_env()` |
| `campaign_additional_loss_usd` | **5.00** | `CBL.limit_from_env()` |
| `maker_pair_ceiling` | **0.96** | `TS.max_pair_cost()` |
| `ts_round_allocations` | **2** | `TS.round_allocations()` |
| `defensive_execution_enabled` | **false** | `DEF.enabled()` |
| `L5_TS_COMPLETION_CROSS` | unset / disabled | — |

### Why 4.64 and not 4.69

The validation gates' own `fee_worst` charges Theta=0.0695, rounds UP to the
cent, and adds `fee_extra_per_order_usd` $0.01 **per order**. Swept over every
price 0.01-0.99, principal 4.69 gives a fee-inclusive worst case of 5.03 (1
leg) to 5.09 (4 legs) -- it BREACHES the $5 ceiling. 4.64 holds for every leg
count 1-4: 4.64 + 0.36 = 5.00 exactly. The earlier 4.9997 figure came from
`l5_pmus_transport.worst_case_from_venue`, which omits the per-order
allowance; the gates are what enforce and they are stricter.

### Why 0.90 and not 0.10

`worst_case_deficit` evaluates the corner where an outstanding complement
NEVER fills, so leg one of a pair is always priced as a strand. Measured
under the campaign's own caps (notional 0.80, max 2 contracts):

    round 1 pair, worst over all prices:   deficit 0.8710  (corner: none)
    round 2 with round 1 fully stranded:   deficit 1.6720  (corner: none)

At 0.10 the campaign places **zero** orders. At 0.90 round 1 opens and round 2
is refused while round 1 is still unmatched -- which is the
`L5_OPERATING_MODEL` rule "no new first leg while unrelated residual exposure
exists", enforced arithmetically rather than by convention.

0.10 was the only bound that refused a pair cost of 1.04 (the unauthorized
2026-10-23 02:39 cross), but no single scalar can both open a pair (needs
>= 0.88) and refuse that completion (needs <= 0.10). What actually forbids the
1.04 cross is `MAKER_COMPLETE_CEILING = 0.96` plus `L5_DEFENSIVE_COMPLETION=0`.

On the real venue-confirmed 1945z strand (UP 1.56 @ own 0.44, acquisition
$0.6864), the 0.90 bound permits maker completion at every pair cost up to
1.02 and refuses above it -- so it does not block legitimate completion.

## The four remaining steps

Steps 1 and 2 are one action. Applying them restarts the container on the
**same image and SHA** -- no build, no code deployment.

1. **`L5_LIVE_AUTH_NOT_AFTER`** = activation time + 3h, ISO-8601 with timezone.

2. **`L5_CAMPAIGN_APPROVAL`** = the output of
   `python3 scripts/l5_campaign_approval_emit.py`

   Verified against the deployed `approval_gate()`: `approved: True`,
   nothing clamped, `nonce_sid camp1.87265e609a92b5a2889e5392`, 4 windows,
   $5 exposure, $5 daily loss, 3h expiry.

   **Staleness is 4 hours, not 1.** `approval_gate()` sets
   `max_age_s = MAX_CAMPAIGN_S = 14400s`; the 1-hour `DEFAULT_MAX_AGE_S`
   applies to a PULL endpoint, not to an owner-set variable. `state()` clamps
   the effective expiry to `issued + 4h` regardless. So the document does not
   have to be minted in the same minute as arming.

   The fresh nonce resets the durable window count to 0/4. The spent
   `camp1.1ae75816714cfe65f0f256ad` is left untouched as the audit record.

3. **Arm** (`PMUS_Q1_ARM`). `owner_arm_token` already PASSES; arming is the
   separate deliberate act.

4. **Confirm the post-restart preflight reads `ok: true`** before any order.

## Preflight state now

`L5_AUTHORIZATION_PREFLIGHT ok:false`, `fail: ["absolute_deadline",
"campaign_approval"]` -- exactly the two documents above, nothing else.

PASSING: `shadow_lock`, `owner_arm_token`, `claim_ttl_s`, `market_series`,
`role_binding`, `session_id`, `window_budget_env` (4), `halt_gate`,
`residual_ceiling`. `L5_BUDGET_SELFCHECK` 14/14. `L5_RESIDUAL_SIDE_SELFCHECK`
7/7. Mandate store `BOTH_DOCUMENTS_FRESH`. Watchdog latch CLEAR, written by
the separate `l5-halt-watchdog` service.

NOT a blocker: `L5_EFFECTIVE_RUNTIME_CONFIG PASS=false`. Its `APPROVED` table
is hardcoded to the superseded 2026-10-21 eight-window campaign, and nothing
reads its verdict -- it is emitted once at boot and consumed by no gate. Six
rows fail against that stale table; `max_windows_authorization` fails because
4 is TIGHTER than its required 8.

## Expected campaign shape

    maker OPEN pair (<= 0.96 combined, <= 0.80 notional, <= 2 contracts)
      -> reassess -> optional same-side EXTEND within round 2
      -> patient fee-safe COMPLETE when price improves
      -> settle -> audit

One market, one outstanding order, maker-only, no defensive crossing, 4
windows, 3 hours, $4.64 principal / $5.00 fee-inclusive worst case, $0.90
worst-case deficit, $5 daily loss, $20 cumulative loss.

## Accounting

Realized P&L is **settlement-only**. Nothing is realized profit until it
settles and reconciles. Current verified position: all-time
**-$13.64050000**, UTC day 2026-10-26 **$0**, campaign **$0.00**, zero fills.
