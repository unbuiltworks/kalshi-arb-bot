# Final preflight and approval request — 8-window two-sided validation

**NOT ARMED. NOT APPROVED. No order submitted, no limit changed, no variable
written.** Read at 12:16–12:17Z on 2026-11-01.

---

## 1. One hard blocker

**Requirement 2 cannot be satisfied right now.** You asked for
`taker_crossing_reachable=false` **from the running process**, not inferred
from configuration. The deployed executor build **has no such row to print** —
I added it, but it is not deployed. A restart alone cannot produce it.

I prepared the minimum deploy that would, and **the push was refused by the
tooling as a Production Deploy.** I have not worked around it. The trigger
edit is reverted, the working tree is clean, and the patch is held at
`/tmp/claude-0/-home-user-kalshi-arb-bot/1ec9ca12-5dab-5253-8ef5-126eaccc8b25/scratchpad/pending_executor_trigger.patch`.

**That deploy would change exactly one file in the executor image** —
`src/l5_runtime_config_verify.py`, +25 lines of read-only reporting.
`src/l5_watchdog.py` and `src/l5_emergency_halt_marker.py` also differ from
the running commit but are **not copied into that image at all** (checked
against its Dockerfile), so they cannot reach the executor.

### An inference I tried and threw away

`L5_SHADOW_COMPLETION` records show the taker policy refusing at
`WORST_OUTCOME_NOT_POSITIVE` and `DECISION_NOT_OK` — gates **4+**, which looks
like proof that gates 1–2 (`POLICY_DISABLED`, `OWNER_AUTHORIZATION_ABSENT`)
were passed, and therefore that crossing is authorized.

**It proves nothing.** `l5_shadow_completion.evaluate` declares
`enabled: bool = True, owner_authorized: bool = True` "because this is a
MEASUREMENT of the policy, not an execution path", and the caller never
overrides them. Those refusals are artifacts of hardcoded defaults. I nearly
reported them as evidence; they carry **no information** about the live
switches.

### Your two options

1. **Authorize the push** (recommended). Then: deploy → disarmed restart →
   read `taker_crossing_reachable` from `L5_EFFECTIVE_RUNTIME_CONFIG`. This is
   what you actually asked for.
2. **Read `L5_TS_COMPLETION_CROSS` in the Railway dashboard yourself.**
   `ts_completion_cross_authorized()` is literally
   `os.getenv("L5_TS_COMPLETION_CROSS") == "YES"`, so the variable *is* the
   ground truth — **with one caveat**: Railway fixes env at process start, so
   the dashboard value equals the running value only if the service has
   restarted since it was last set. Weaker than option 1, and it is the
   "configuration alone" you told me not to rely on.

**Until this resolves, I am not proposing to arm.** If it reads `YES`, set it
to anything else and restart before arming.

## 2. Preflight — everything else PASSES

### Simultaneous quoting active in the running process — **PASS**

`L5_EFFECTIVE_RUNTIME_CONFIG`, the executor's own accessors:

```
ts_two_order_pair_enabled    TS.two_order_pair_enabled()   true
window_strategy              env L5_WINDOW_STRATEGY        TWO_SIDED    ok
max_live_orders              R.max_live_orders()           1            ok
ts_opening_leg_max_contracts                               "3"
ts_min/max_first_leg_own_price                             "0.20"/"0.60"
```

`max_live_orders` stays **1**; `second_leg_slot_refusal` raises it by exactly
one, only for the opposite leg of the same round in the same market.

### Emergency cancellation deployed — **PASS**

Watchdog deployment **`5ef90355`**, sweep at **12:17:13Z**:

```
"emergency_cancel": false, "emergency_reason": null, "cancel_enabled": true,
"open_orders": 0, "overdue": [], "not_ours": [], "still_open": [],
"unverified": [], "read_error": null, "heartbeat": {"halt": false, ...}
```

The fields exist, so the branch is reached and evaluated every 20s cycle.

### Zero venue orders and unresolved obligations — **PASS**

| source | value |
|---|---|
| watchdog `open_orders` / `still_open` / `unverified` / `not_ours` | **0 / [] / [] / []** |
| executor `live_orders` | **0** |
| executor `committed_usd` | **`{}`** — empty, no capital committed to any market |
| executor `unresolved_accounting.count` | **0** |

One field needs disambiguating: the heartbeat contains
`"stranded": {...,"inventory":{"yes":1,...}}`. That sits inside a block
labelled *"MODELLED by the queue-aware model from venue settlement prices.
NOT realized, NOT filled, NOT submitted"* — it is a shadow arm, **not our
position**.

### Limits held at the approved values — **PASS**

| limit | running value |
|---|---|
| aggregate exposure | **$20** |
| per-market worst-case deficit | **$1.50** |
| daily settled-loss halt | **$5** |
| residual cap | **3 contracts** (enforced by `residual_cap_decision`; binds at 3.01) |
| max live orders | **2**, via the pair slot only |
| execution | maker-only, `maker_pair_ceiling 0.96` |

### Current state

| | |
|---|---|
| executor | deployment `0072e385`, commit `00a78b50` |
| watchdog | deployment `5ef90355`, commit `2a56a152` (carrying `ebaef2d8`) |
| `armed` / `owner_arm_token` / gate `blocked` | **false / false / true** |
| `halt_latch` | `CLEAR` |
| `approval_state` | `approval_stale` — no authorization in force |
| `all_time_realized_usd` | **−8.82910000** |

## 3. The exact eight-window schedule

| # | window | start (UTC) | end (UTC) |
|---|---|---|---|
| 1 | `1600z` | 16:00:00 | 16:15:00 |
| 2 | `1615z` | 16:15:00 | 16:30:00 |
| 3 | `1630z` | 16:30:00 | 16:45:00 |
| 4 | `1645z` | 16:45:00 | 17:00:00 |
| 5 | `1700z` | 17:00:00 | 17:15:00 |
| 6 | `1715z` | 17:15:00 | 17:30:00 |
| 7 | `1730z` | 17:30:00 | 17:45:00 |
| 8 | `1745z` | 17:45:00 | **18:00:00** |

All on **2026-11-01**, series `cpc-btc-updown-15m-`. Eight windows, 2h00m.

**Both boundaries are closed.** `starts_utc = 16:00:00Z` refuses any window
starting earlier (`WINDOW_BEFORE_AUTHORIZED_START`).
`L5_LIVE_AUTH_NOT_AFTER = 17:45:00Z` is the last window's **start**, so an
1800z window cannot be absorbed — the Stage B2 15:00Z hole, shut from both
ends.

## 4. The authorization document

**Validated by dry run.** I ran the real generator against the real gate with
these parameters and **nothing was clamped** — `max_windows 8`, exposure $20,
daily loss $5 and the expiry all passed through unchanged. The dry-run file
carried a nonce and has been destroyed; no nonce has ever been printed.

**The budgets deliberately match the variables already deployed**
(`32 / 32 / 8 / 56`), so **no budget variable needs changing**. The campaign
needs only 16 OPEN and 32 COMPLETE, so the deployed values are loose enough
and the runtime verifier will grade document against variables and pass.

**Run at 15:30:00Z** — not before. `expires_utc = issued + ASK_DURATION_S`,
clamped to `issued + 4h`. Issued at 15:30Z with 9000s, expiry lands exactly on
**18:00:00Z**. Issued now it would expire at 16:19Z, two windows in.

```bash
ASK_WINDOWS=8 \
ASK_EXPOSURE_USD=20 \
ASK_DAILY_LOSS_USD=5 \
ASK_DURATION_S=9000 \
ASK_OPEN=32 ASK_COMPLETE=32 ASK_REPLACE=8 ASK_TOTAL=56 \
ASK_STARTS_UTC="2026-11-01T16:00:00+00:00" \
PYTHONPATH=. python3 tools/make_campaign_approval.py /secure/path/approval.json
```

stdout carries the sha256 and the non-secret fields; the nonce goes only to
the file. Paste the file's contents into `L5_CAMPAIGN_APPROVAL`.

## 5. The precise approval request

**Owner variable writes, in this order.** Each is yours — the executor cannot
write its own authorization (`EnvApprovalStore.put` refuses by construction).

| # | variable | value | note |
|---|---|---|---|
| 0 | — | — | **Resolve §1 first.** Confirm `taker_crossing_reachable=false`. |
| 1 | `L5_TS_ROUND_ALLOCATIONS` | `2` | currently **4**. At 4, first legs alone consume OPEN 32 exactly and second legs COMPLETE 32 exactly, leaving **zero** headroom for repairing a half-filled pair. 2 leaves 16 COMPLETE for repairs. |
| 2 | `L5_LIVE_AUTH_NOT_AFTER` | `2026-11-01T17:45:00+00:00` | currently `14:58:00Z`, left from Stage B2 |
| 3 | `L5_CAMPAIGN_APPROVAL` | contents of the file from §4 | minted at 15:30Z |
| 4 | `PMUS_Q1_SESSION_ID` | a fresh id | |
| 5 | `PMUS_Q1_ARM` | the **same** id | |
| 6 | `PMUS_Q1_LIVE` | `YES` | arming — last |

**A warning about every one of these.** A variable write on this service
**bypasses `watchPatterns` and rebuilds branch HEAD**, ignoring the
`commitSha` pin. Branch HEAD on origin is `f6d27d0a`, which already contains
the observability change — so **write 1 would deploy it as a side effect.**
I am naming that rather than using it: it would be a way around the refusal
in §1, and you should decide deliberately, not inherit it.

**Not requested:** no capital increase, no new service, no new branch, no
crossing, no residual-cap change, no `MAX_WINDOWS` change.

## 6. Disarming — latch first

Clearing `PMUS_Q1_ARM` takes effect **only at restart**. In Stage B2 the write
landed and window 8 armed **52 seconds later anyway**, trading until the
restart — 5m53s of unintended trading.

1. **To stop fast, latch the HALT.** Read every tick, and it now drives
   emergency cancel-all on fresh orders.
2. Clear `PMUS_Q1_ARM` afterwards, for the next boot.
3. Verify `armed false`, `live_orders 0`, watchdog `open_orders 0`.

## 7. What I will not do without you

Arm, submit, change a limit, write any variable above, or push the §1 deploy.
