# `L5_WINDOW_STRATEGY = L5_FOLLOWING` — applied, loaded, and not yet observable

## 1. The change — applied, nothing else touched

`L5_WINDOW_STRATEGY`: `TWO_SIDED` -> **`L5_FOLLOWING`** on `l5-pmus-live`, written with
`skipDeploys: true`, then **deployment `bab0bfaa`'s existing build redeployed** (new deployment
**`470a8d2b`**, status SUCCESS, commit **`118365b0`** — unchanged). The code is byte-identical and
the variable is the only difference, which is what makes the result attributable. I did not build
the branch head; that would have pulled in 39 commits never authorized for this executor.

Untouched: `L5_SHADOW_ONLY`, order sizing, ceilings, credentials, risk limits. No new service,
branch or strategy.

## 2. The running process really loaded it — verified from the money path, not from Railway

`L5_EFFECTIVE_RUNTIME_CONFIG` reports *"the value the money path's own accessor returns, not the
text of a variable"*:

```json
{"name":"window_strategy","source":"env L5_WINDOW_STRATEGY",
 "effective":"L5_FOLLOWING","required":"TWO_SIDED","ok":false}
```

`shadow_only` **true**, `defensive_execution_enabled` **false**, and every risk row identical:
exposure $5 (and $5 frozen at boot), daily halt $5, opening notional $0.80, `max_live_orders` 1,
`maker_pair_ceiling` 0.96, worst-case deficit $2.00, series allowlist BTC15m only.

## 3. The window capture is NOT available, and the reason is structural

**`L5_SHADOW_ONLY` and the window capture are mutually exclusive on this engine.** Items 1 and 3 of
the instruction cannot both be satisfied, and that is a property of the code, not a failure of the
switch.

```
L5_LIVE_SHADOW_LOCK {"locked":true,"env":"L5_SHADOW_ONLY","blocked":["submit"],
  "cancel_still_permitted":true,
  "labels":"submit() raises NotArmed regardless of PMUS_Q1_LIVE/SESSION_ID/ARM..."}

L5_LIVE_DISARMED {"market":"cpc-btc-updown-15m-2026-10-26-0145z","why":"SHADOW_ONLY_LOCK",
  "detail":{"note":"submit() is structurally blocked; arming would change nothing"},
  "boundary":"l5_live_authorization.authorize_arming"}
```

The engine **disarms at the authorization boundary** because submit is blocked, so it never reaches
`translation`. Deployment `470a8d2b` has produced **zero `L5_LIVE_DECISION` rows**; the only labels
on it are `L5_LIVE_SHADOW_LOCK`, `L5_LIVE_DISARMED` and `L5_LIVE_HB`.

This is the same mechanism as the Oct 20 evidence, seen from the other side: those refused rows
carried `translation.reason: STRICT_NOT_EVALUABLE` and `safety: null`. **Side/price/size/timing
agreement cannot be measured until the path is permitted to evaluate.** I am not going to estimate
it.

The engine is otherwise tracking the live window correctly — heartbeat on
`cpc-btc-updown-15m-2026-10-26-0145z`, `preflight: "PASSED"`, `owner_arm_token: true`,
`halt_latch: "CLEAR"`, `armed: false`, `submission_gate.blocked: true, reason: SHADOW_ONLY_LOCK`.

## 4. Loss-gate headroom — read without exposing any secret

From the live `L5_LIVE_GATES` row (01:46:24Z), via the gates object's own telemetry:

| gate | limit | used | headroom |
|---|---|---|---|
| **L5-scoped cumulative** | **$20** | $5.2457 | **$14.7543** |
| account-wide cumulative | $20 | $5.2457 | $14.7543 |
| L5-scoped daily (today) | $20 | $0 | $20 |
| **account-wide daily halt (today)** | **$5** | $0 | **$5.00** — binding |

Prior campaign budget `camp1.1c23068c…`: budget $29.30, peak $15.2005, **remaining $14.0995**,
`incremental_room_usd` $15.6595. **Active `campaign_id`: null.**

**This closes the open question and reverses my earlier caution: the cumulative limit is $20, not
$5, so it does not block.** `max_cash_deployed_usd` $5, `max_unmatched_contracts` 5,
`cash_policy` NO_RECYCLING, `max_worst_case_deficit_usd` $2.00.

## 5. Remaining mismatches

**None that prevent live execution.** The only thing blocking submission is `SHADOW_ONLY_LOCK` —
which is what you asked to keep. Preflight PASSED, halt latch CLEAR, arm token present, every loss
gate has headroom.

**One reporting mismatch, flagged not fixed.** The verifier's expected value for `window_strategy`
is still hardcoded `TWO_SIDED`, so it now reports `ok: false` against the owner-approved strategy,
taking the failing-row count from 5 to 6. It gates nothing — `CFGV.verify()` is called once inside
`emit(...)` and `PASS` is read only as a CLI exit code — but it is stale and would mislead the next
reader into reverting the strategy. Fixing it is a one-line change to a reporting module. **I have
not made it, because the instruction was to change only `L5_WINDOW_STRATEGY`.**

## 6. Exact requirements for the bounded live campaign

Nothing to change in code, sizing, ceilings, credentials or risk limits. Required:

1. **A fresh campaign approval document** — `approved: true`, a new `nonce`, `series`
   `cpc-btc-updown-15m-`, `issued_utc`, `expires_utc`, `max_windows 3`,
   `max_total_exposure_usd 5`, `max_daily_loss_usd 5`. The Oct 25 document expired 20:30Z and the
   active `campaign_id` is null.
2. **`L5_SHADOW_ONLY=0`** — the single submission blocker, and the only way the L5-following path
   can evaluate and produce the agreement record you want.
3. **The arming triple** — `PMUS_Q1_LIVE=YES`, a fresh `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` equal to
   it.
4. **A redeploy** to load (1)–(3), since a variable write does not restart the process.

Budget available inside existing limits: **$5.00** daily (binding), **$14.7543** cumulative,
**$14.0995** campaign. Exposure stays $5, notional $0.80, one live order, 0.96 ceiling.

**Next concrete window:** the first BTC15m open after activation — they run :00/:15/:30/:45.

**Nothing is armed. I did not set `L5_SHADOW_ONLY=0` or the arming triple.**
