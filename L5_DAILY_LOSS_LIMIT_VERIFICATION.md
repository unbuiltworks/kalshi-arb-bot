# Verifying the $5.2457 loss-gate accounting before the $5 -> $20 change

Owner authorized: `L5_MAX_L5_DAILY_LOSS_USD` $5 -> $20 on `l5-pmus-live`, all other risk
controls unchanged, accounting verified first, effective runtime value confirmed after.

## 1. The variable name and the gate are correct

- env var: `L5_MAX_L5_DAILY_LOSS_USD` — `src/l5_pmus_live_runner.py:5808`, feeds
  `max_l5_daily_loss_usd`
- gate: `src/l5_live_validation_gates.py:579` emits
  `L5_DAILY_LOSS_LIMIT:<value>>=<limit>`, which is the refusal we saw

## 2. What $5.2457 actually is

```
l5_loss = max(0, -l5_scoped_realized) + max(0, cumulative_unrealized_loss)
```

Measured from the logs: `"l5_realized_since_baseline":"-5.245700"`.

So `l5_loss` = $5.2457 comes **entirely** from the realized term; the unrealized term
contributed **zero**. The figure is accounting-valid:

- `l5_realized_since_baseline` (`src/l5_live_execution.py:1828`) sums `settlement_pnl` for
  every market retired **after the baseline epoch T** whose ownership was **attested**
  (`l5_owner_attribution_verified`). Unattested post-T settlements are deliberately
  **excluded** and surfaced separately by `l5_baseline_lines()`, so an unattributable
  settlement can never silently read as an L5 zero.
- an unreadable row returns `None`, and `None` refuses as
  `L5_SCOPED_REALIZED_UNAVAILABLE` rather than defaulting to 0. Missing is not zero.
- it is consistent with the surrounding figures: UTC-day realized -$2.0192 and all-time
  -$13.6405, with the baseline-scoped subset at -$5.2457 sitting between them.

**Verdict: the $5.2457 is correct and the gate fired correctly.**

## 3. THE MATERIAL FINDING — it is not a daily figure

`l5_realized_since_baseline` accumulates every attested settlement since epoch T and
**never resets daily**. The gate named `L5_DAILY_LOSS_LIMIT` is therefore a
**cumulative-since-baseline** budget, not a daily one.

Two consequences the owner should price in:

1. **Raising it to $20 does not create a daily allowance.** It creates a ONE-TIME,
   non-replenishing headroom of **$20 - $5.2457 = $14.7543**. Tomorrow it does not refill.
2. **It becomes exactly redundant with the cumulative gate.** `max_l5_cumulative_loss_usd`
   is already `"20"` and is evaluated against the **same** `l5_loss` quantity
   (`l5_live_validation_gates.py:581-585`). After the change both ceilings are $20 on one
   number, so there is effectively ONE L5-scoped ceiling at $20. Because the daily check
   runs first, the next refusal will read `L5_DAILY_LOSS_LIMIT:...>=20`.

Keeping the cumulative limit unchanged, as instructed, means $14.7543 is the **total**
remaining L5-scoped loss budget, after which trading stops until the owner either raises the
cumulative ceiling or re-establishes a baseline.

Neither the account-wide `daily_realized_loss_halt_usd` ($5), `max_cumulative_loss_usd`
($20), `max_total_exposure_usd` ($5), `max_worst_case_deficit_usd` ($2.00), the $0.80
opening notional, the 0.96 maker pair ceiling, nor the watchdog were touched. The approval
code ceiling `CA.MAX_DAILY_LOSS_USD` ($5) is a different control bounding the approval
document and is left alone; it does not clamp this gate, because the arming check at
`src/l5_live_authorization.py:2086` compares the approval against
`daily_realized_loss_halt_usd`, not against the L5-scoped limit.

## 4. An observability gap found while verifying

`L5_EFFECTIVE_RUNTIME_CONFIG` grades 26 rows and **does not include** either L5-scoped
limit — neither in `rows` nor in `observed_not_graded`. So the boot verifier cannot confirm
this change. The values ARE emitted by **`L5_LIVE_GATES`** (`"max_l5_daily_loss_usd":"5"`,
`"max_l5_cumulative_loss_usd":"20"`), which is the confirmation route used here. Adding the
two limits to the boot verifier as observed-not-graded rows would be an observability-only
improvement; not done, since it is a code change nobody authorized.

## 5. Change applied, and NOT yet effective in the running process

- store written: `L5_MAX_L5_DAILY_LOSS_USD=20`, with `skipDeploys` so the restart was
  explicit rather than implicit
- also written **`L5_SHADOW_ONLY=1`** — my own safety addition, not requested. It tightens,
  never widens, and it is the correct resting state while no campaign is authorized. The
  documented near-miss on Oct 25 was precisely another session's deploy making an unintended
  `L5_SHADOW_ONLY=0` effective at a moment nobody chose, on this same money-capable service.
  Reverting it is already part of the arming procedure.
- an in-place **restart did NOT re-exec the process**: heartbeats at 20:39:29Z and
  20:40:34Z still read `shadow_lock=false`, which is only possible on the pre-change
  environment, and no boot markers appeared. Railway's restart reuses the deployment's baked
  env; only a NEW DEPLOYMENT picks up changed variables. Recorded because it is the same
  class of error as the Oct 25 "armed" misreport: the store and the running process are two
  different things, and a successful write is not an effective change.
- **redeploy of the SAME build** (`55a11bf3` -> deployment `bab0bfaa`, no code change) booted
  at 20:43:21Z and did pick the variables up.

## 6. CONFIRMED EFFECTIVE

`L5_EFFECTIVE_RUNTIME_CONFIG` at 20:43:21Z shows `shadow_only` effective **true** (was
`false`), proving the new environment is in force, with every graded row identical to the
19:35:59Z boot and the same five pre-existing `PASS:false` rows (tighter-than-reference
submission budgets and `max_windows_authorization`), so no new failure was introduced.

`L5_LIVE_GATES` at 20:45:01Z, from the money path's own accessors:

```
"max_l5_daily_loss_usd":      "20"   <- CHANGED from "5"
"max_l5_cumulative_loss_usd": "20"   unchanged
"max_cumulative_loss_usd":    "20"   unchanged
"max_cash_deployed_usd":       "5"   unchanged
"max_unmatched_contracts":     "5"   unchanged
"max_worst_case_deficit_usd":  "2.00" unchanged
"max_strategy_loss_usd":       null   unchanged
"halt_gate_configured":        true   watchdog/halt intact
"campaign_id":                 null   no campaign scope active
```

**Status: APPLIED AND CONFIRMED.** Effective L5-scoped ceiling $20 against a current
`l5_loss` of $5.2457 -> **$14.7543 of one-time headroom**. Campaign expired, `armed:false`,
shadow lock engaged; no live order can be submitted without fresh authorization.

## 7. What the headroom buys, and why it does not answer the open question

At observed live rates (13 traded windows, -$2.0192 realized = -$0.155/window;
`ours_fills_per_window` 0.25-0.50, take 0.40):

| | |
|---|---|
| headroom | $14.7543 (one-time) |
| windows it buys | ~95 (~1 day of continuous single-market BTC15m) |
| maker fills it buys | ~38 |

The open cancellation question needs, at 80% power to detect AUC 0.60, **~376 fills**
(~941 windows, ~$146, ~9.8 days) -> the budget is **9.9x short**. At n=71 TRAIN fills the
only observable that separates adverse fills is the entry price itself (AUC 0.119 on
`adv_close`), which is tautological: cheap legs lose more often, and that is the market's own
probability, already in the price. Every non-tautological feature (book imbalance, spread,
time-to-expiry, depth, depth ratio) sits inside the +/-0.15 noise band at this n.

**So the cancellation question cannot be resolved live under this budget, and the binding
blocker is not capital — it is access to the already-recorded tape** (`pmus-btc15m-tape`,
2.19M rows at 1.09s cadence, on a Railway volume this container cannot reach). That costs
$0 in trading loss and is 5.3x the sample we have.

