# Stage 1 live authorization request — $5 / $5

**Not active. Awaiting explicit owner approval of this exact scope.**

## Pre-activation proofs

### 1. The exposure gate aggregates account-wide — proven, not asserted

`SafetyLimits.refuse` computes, in `src/l5_live_execution.py`:

```python
total = (sum(committed_by_market.values(), D(0))
         + sum(outstanding_by_market.values(), D(0)))
if total + cost > self.max_total_exposure_usd:
    return "MAX_TOTAL_EXPOSURE"
```

`sum(...values())` spans **every** market, over **both** components —
`committed_by_market` is filled notional, `outstanding_by_market` is
still-fillable resting orders valued on `leaves_qty`, so a **partial fill
contributes its filled part to the first and its unfilled remainder to the
second**, with no gap and no double count. The proposed order's `cost` is added
on top. The risk-model branch is likewise `sum(risk_by_market.values())`.

Executed against a $5 ceiling, all five cases behave correctly:

| scenario | result |
|---|---|
| filled inventory $4.80 in **another** market + this $0.35 order | `MAX_TOTAL_EXPOSURE` |
| outstanding orders $4.80 in **another** market, nothing filled | `MAX_TOTAL_EXPOSURE` |
| filled $2.40 + outstanding $2.40, **each individually under the cap** | `MAX_TOTAL_EXPOSURE` |
| $1.60 across **three different markets**, none near the cap | `MAX_TOTAL_EXPOSURE` |
| account total $2.35 against $5 | **allowed** |

`max_usd_per_market` is a *separate, additional* per-market bound. There is no
per-market-only path to the account ceiling.

### 2. Worst-case loss from actual prices, not a representative one

Own-side prices that actually filled in the campaign: 0.24, 0.29, 0.35, 0.35,
0.37, 0.56, 0.59, 0.59. At the Stage 1 clip of **3 contracts**:

| own price | cost | loss if it settles worthless |
|---|---|---|
| 0.24 | 0.7200 | −0.7200 |
| 0.35 | 1.0500 | −1.0500 |
| 0.37 | 1.1100 | −1.1100 |
| 0.56 | 1.6800 | −1.6800 |
| **0.59** (worst observed) | **1.7700** | **−1.7700** |
| **0.95** (structural worst the 0.96 cap permits) | **2.8500** | **−2.8500** |

**Correction to my earlier estimate.** I previously said "roughly five adverse
residuals" reach the $5 daily halt, computed from the *average* residual cost
of ~0.30/contract. From actual prices the picture is tighter:

* at the **average** observed residual (~$0.90 at 3 contracts): **5.6** losses
* at the **worst observed** fill (0.59 → $1.77): **2.8** losses
* at the **structural worst** the cap permits (0.95 → $2.85): **1.8** losses

So **two structural-worst windows breach the $5 daily halt**, and a single such
position commits **$2.85 of the $5 exposure ceiling — 57%**. That is the
binding reality of Stage 1 and it is the main thing I would want the owner to
weigh.

### 3. Runtime state, from the running process at 2026-10-24T12:33:11Z

| check | reading |
|---|---|
| `open_orders_at_venue` | **0** |
| `live_orders_pending_terminal` | **[]** |
| `pending_terminal_total` | **0** |
| `quarantined` | **[]** |
| `uncertainty_cleared` | **true** |
| `ALERT` | **null** |
| `armed` | **false** (campaign exhausted *and* expired) |
| `realized_today` | **−7.90190000** — matches the campaign reconciliation exactly |
| venue pacing | 5,138 requests over 32,980 s uptime, `http_429: 0`, no cooloff |
| `L5_BUDGET_SELFCHECK` | `all_passed: true` at boot |
| watchdog service | last deployment SUCCESS |
| authorization ledger | `durable: true` |

**Two disclosures rather than a clean-slate claim:**

* **Standing drift, pre-existing:** `2026-10-12-0030z|DOWN` (ours 2, venue 0)
  and `2026-10-14-2230z|UP` (ours 2.9, venue 0), both flagged
  `gross_detail_lost` with the venue authoritative at **zero**. They carry no
  live exposure and predate this work, but they are a standing
  gross-versus-net discrepancy and the owner's condition 4 should be read with
  them in view. `leg_cost_basis_unknown` likewise lists ~49 historical legs.
* **`journal_errors` and `incremental_room_usd` are NOT yet observed in
  production.** Both ride `L5_LIVE_GATES`, which is emitted from the executor,
  and the executor is only constructed when arming succeeds. They are in the
  running image (`cc645589`) and covered by 30 tests including induced
  failures, but the first production reading will come on the first arm of
  this stage. I am not claiming an observation I do not have.

### 4. Fail-closed on uncertainty — verified ordering

`SafetyLimits.refuse` returns `ORDER_STATE_UNCERTAIN` **first**, ahead of the
exposure ceiling, the live-order ceiling and the loss halts. Reconciliation
discrepancy sets the store UNCERTAIN via `L5_LIVE_RECONCILE`, and
`authorize_arming` refuses `ORDER_STATE_UNCERTAIN` at the arming boundary —
observed doing exactly that at 00:33:38Z on an unbooked terminal order.

## The authorization scope, exactly

| parameter | value |
|---|---|
| campaign id | `camp1.l5-btc15m-stage1-<fresh nonce>` |
| market series | `cpc-btc-updown-15m-` |
| **cash envelope** | **$5.00** *(from $1.65)* |
| **account-wide exposure ceiling** | **$5.00** *(from $2.00)* |
| absolute residual ceiling | **3 contracts** — unchanged |
| `max_live_orders` | **1** — unchanged |
| `max_concurrent_markets` | **1** — unchanged |
| `max_usd_per_market` | **$20** — unchanged |
| pair cost cap | **0.96** — unchanged |
| execution mode | **MAKER_ONLY, post-only** — unchanged |
| daily realized-loss halt | **$5** — unchanged |
| cumulative realized-loss halt | **$20** — unchanged |
| campaign worst-case budget | **$29.30**, incremental room $21.40 |
| `L5_TS_ROUND_ALLOCATIONS` | **1** — two-round experiment stays undeployed |
| two-order pair mode | **disabled** |
| 0.20 entry floor | **disabled** |
| **eligible windows** | **16** |
| **start** | on owner approval, first eligible window boundary |
| **expiry** | **issue + 5h30m**, explicit UTC, giving 22 slots for 16 windows |

**Maximum outstanding cash:** $5.00, enforced as
`filled (unreleased) + still-fillable outstanding + the proposed order`,
account-wide, with no release before venue settlement.

**Maximum worst-case exposure:** $5.00 account-wide. Worst single-window
realized loss **$2.85** (3 contracts at the dearest price the 0.96 cap
permits); worst observed-price equivalent **$1.77**. Two structural-worst
windows trip the $5 daily halt.

## Pre-registered validation — not matched-pair surplus alone

Minimum **16 consecutive eligible windows**. Promotion is judged on **total
venue-settled realized net P&L** as the primary measure, with all four
reported separately and none substituting for another:

1. **Total venue-settled realized net P&L** — the primary measure.
2. **Residual settlement losses** — count, per-contract cost, and total.
3. **Drawdown** — worst cumulative realized trough across the 16 windows.
4. **Execution quality** — fill rate by leg role, cancel→terminal latency from
   the new emitter clock, queue-ahead at submission, and recovery outcomes
   separated into first-leg and complement.

Matched-pair surplus is reported but is **explicitly not sufficient** for
promotion. Rebate estimates are reported in their own column and never summed
into realized.

## Rollback procedure

1. **Immediate stop, no deploy:** set `L5_LIVE_AUTH_NOT_AFTER` to a past UTC
   instant. Arming then refuses `AUTHORIZATION_EXPIRED` at step 8, before the
   window claim is consumed, so unspent windows are preserved. This is the
   mechanism already used at 00:29Z and verified.
2. **Revert the two raised limits:** set `L5_MAX_CASH_DEPLOYED_USD=1.65` and
   `L5_MAX_TOTAL_EXPOSURE_USD=2`. Both are refuse-only ceilings; lowering them
   cannot release existing exposure, only forbid new.
3. **Code rollback, if ever needed:** no trading-policy code changes are in
   this stage, so there is nothing to revert. Stage 1 is **entirely
   configuration**. `da0ca5ac` stays undeployed.
4. **Standing protections, unchanged throughout:** the watchdog, exact-order
   cancellation by venue id, the deadman, `ORDER_STATE_UNCERTAIN` fail-closed,
   and both loss halts continue to operate during and after any rollback.
5. **Settlement obligation:** any inventory held at rollback is carried to
   venue settlement under the unchanged residual ceiling; nothing is force-
   exited, since maker-only execution has no defensive taker path.

Nothing is active. This document requests approval; it does not assume it.
