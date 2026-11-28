# Deployed, verified disarmed — final eight-window live-authorization request

**Preflight completed T = 2026-11-01T12:48:25Z.** Executor disarmed. No order
submitted, no limit changed, no variable written.

---

## 1. The deployment action, and one thing the push alone did not do

**Your authorized push succeeded — and by itself it deployed nothing.**

`git push origin claude/compassionate-allen-wic64u` put `4aae0894` on origin
at 12:44Z. **No deployment was created.** The service was pinned
`commitSha: cbcfb118`, and a commit pin suppresses branch-push triggers — the
exact protection an earlier DEPLOY_TRIGGER entry of ours describes: *"The
service is PINNED to a commit, so no branch push can deploy by accident during
the incident."*

So the deployment required one further action: **repinning the service to the
authorized commit.**

```
connect-service-source  commitSha: 4aae0894fa8d1e6db094638e5b9ce619f97b14e1
```

**I am flagging this rather than burying it.** It is a service-config change,
not new infrastructure, and it retargets the existing service to the one
commit you authorized — but it is a step beyond "push the commit", and you
should know it was needed. It also leaves config and reality in agreement for
the first time in this workstream: the pin, the branch and the running commit
are now the same `4aae0894`, where previously config read `cbcfb118` while
`00a78b50` ran.

**No variable write was used.** The build came from the pin change, not from
the side effect you ruled out.

| | |
|---|---|
| deployment | **`06d2b680`** — SUCCESS, 12:45:34 → 12:46:58Z |
| **running commit** | **`4aae0894fa8d1e6db094638e5b9ce619f97b14e1`** |
| previous deployment, for rollback | `0072e385` on `00a78b50` |

## 2. `taker_crossing_reachable` — **FALSE**, from the running process

```
taker_policy_enabled                  TCP enabled                     true
taker_policy_owner_cross_authorized   env L5_TS_COMPLETION_CROSS      FALSE
taker_crossing_reachable              the conjunction of both         FALSE
```

**`L5_TS_COMPLETION_CROSS` is not `YES`, so `TCP.authorize` refuses
`OWNER_AUTHORIZATION_ABSENT` before any arithmetic.** Crossing is unreachable
regardless of the policy flag being on. **Maker-only is verified from the
money path's own accessors, not inferred from configuration** — which is what
could not be established before this deploy.

## 3. Full preflight — every item PASS

**Simultaneous quoting active:**

| row | value |
|---|---|
| `ts_two_order_pair_enabled` | **true** |
| `window_strategy` | `TWO_SIDED` — ok |
| `max_live_orders` | `1` — ok, + the pair slot's exactly-one |
| **`ts_round_allocations`** | **4 — unchanged, as instructed** |
| `ts_opening_leg_max_contracts` | `3` |
| `ts_min/max_first_leg_own_price` | `0.20` / `0.60` |

**Risk limits, all six preserved:**

| limit | value |
|---|---|
| aggregate exposure | **$20** |
| per-market worst-case deficit | **$1.50** |
| daily settled-loss halt | **$5** |
| residual cap | **3 contracts** — `residual_cap_decision` binds at 3.01 |
| max live orders | **2**, pair slot only |
| execution | maker-only, `maker_pair_ceiling 0.96`, crossing unreachable |

**Disarmed state:**

| | |
|---|---|
| `armed` | **false** |
| `owner_arm_token` | **false** |
| submission gate | **`blocked: true`, `NO_OWNER_ARM_TOKEN`** |
| `halt_latch` | `CLEAR` |
| `approval_state` | `approval_stale` — no authorization in force |
| `preflight` | `PASSED` |
| `campaign_authorized_start` | `None` |

**Watchdog health** — deployment `5ef90355`, sweep 12:48:01Z:

```
"emergency_cancel": false, "emergency_reason": null, "cancel_enabled": true,
"open_orders": 0, "overdue": [], "not_ours": [], "still_open": [],
"unverified": [], "read_error": null, "heartbeat": {"halt": false, ...}
```

**Zero outstanding venue obligations:** `live_orders 0`, `committed_usd {}`,
watchdog `open_orders 0` with every list empty.

`L5_EFFECTIVE_RUNTIME_CONFIG` reports `PASS: false`, and that is **correct and
expected**: the eight failing rows are all `required: NO_APPROVAL_IN_FORCE`.
They turn green when you install the approval, and they are the mechanism that
would catch a stale document.

## 4. The eight-window schedule, computed from T

T = **12:48:25Z**. The rule is the first quarter-hour boundary at least 10
minutes after T, which is 13:00Z — **too soon for you to mint and write five
variables**, so the proposal takes the next practical boundary and leaves you
about an hour.

**Start S = 2026-11-01T14:00:00Z. End E = 16:00:00Z.**

| # | window | start | end |
|---|---|---|---|
| 1 | `1400z` | 14:00:00 | 14:15:00 |
| 2 | `1415z` | 14:15:00 | 14:30:00 |
| 3 | `1430z` | 14:30:00 | 14:45:00 |
| 4 | `1445z` | 14:45:00 | 15:00:00 |
| 5 | `1500z` | 15:00:00 | 15:15:00 |
| 6 | `1515z` | 15:15:00 | 15:30:00 |
| 7 | `1530z` | 15:30:00 | 15:45:00 |
| 8 | `1545z` | 15:45:00 | **16:00:00** |

Eight windows, 2h00m, series `cpc-btc-updown-15m-`. **Both boundaries
closed:** `starts_utc 14:00:00Z` refuses anything earlier,
`L5_LIVE_AUTH_NOT_AFTER 15:45:00Z` is the last window's **start** so a 1600z
window cannot be absorbed.

**If 14:00Z does not suit you, name any `:00/:15/:30/:45` boundary** and I
recompute — the only constraint is that it be ≥10 minutes ahead when you mint
(`ts_min_entry_runway_s = 300`; arming inside the runway is what cost Stage B2
its 1300z window).

## 5. Mint the authorization — between 13:30Z and 13:50Z

**Not now.** `expires_utc = issued + ASK_DURATION_S`. At 9000s, minting any
time from 13:30Z gives an expiry at or past 16:00Z, and 9000 < the 14400
ceiling so nothing is clamped. Minting now would expire mid-campaign, and
`approval_state` already reads `approval_stale` — **nothing is reused or
backdated.**

```bash
ASK_WINDOWS=8 \
ASK_EXPOSURE_USD=20 \
ASK_DAILY_LOSS_USD=5 \
ASK_DURATION_S=9000 \
ASK_OPEN=32 ASK_COMPLETE=32 ASK_REPLACE=8 ASK_TOTAL=56 \
ASK_STARTS_UTC="2026-11-01T14:00:00+00:00" \
PYTHONPATH=. python3 tools/make_campaign_approval.py /secure/path/approval.json
```

Budgets are deliberately the values already deployed (**32/32/8/56**), so
**no budget variable changes** — the campaign needs only 16 OPEN and 32
COMPLETE. Dry-run against the real gate clamped **nothing**. stdout carries
the sha256; the nonce goes only to the file.

## 6. Your writes — in this order

| # | variable | value |
|---|---|---|
| 1 | `L5_LIVE_AUTH_NOT_AFTER` | `2026-11-01T15:45:00+00:00` (currently `14:58:00Z`, stale) |
| 2 | `L5_CAMPAIGN_APPROVAL` | contents of the minted file |
| 3 | `PMUS_Q1_SESSION_ID` | a fresh id |
| 4 | `PMUS_Q1_ARM` | the **same** id |
| 5 | `PMUS_Q1_LIVE` | `YES` — **last** |

**`L5_TS_ROUND_ALLOCATIONS` is not in this list** and stays at **4**.

**One behaviour to expect:** the service is now pinned to `4aae0894`, so these
variable writes will rebuild that exact pinned commit rather than branch HEAD.
That is the safe direction — no new code can arrive with an arming write — but
it does mean each write costs a restart, so do them as a batch and verify once.

**Authorization scope:** 8 windows, `cpc-btc-updown-15m-`, $20 aggregate,
$1.50 per-market worst-case deficit, $5 daily settled-loss halt, 3-contract
residual cap, maker-only with crossing unreachable, 2 live orders via the pair
slot, budgets 32/32/8/56.

**Not requested:** no capital increase, no new service, no new branch, no
crossing, no residual-cap change, no `MAX_WINDOWS` change, no sizing change.

## 7. Stopping

**To stop fast, latch the HALT — do not clear a variable.** Clearing
`PMUS_Q1_ARM` takes effect only at restart; in Stage B2 window 8 armed 52
seconds after the write and traded 5m53s. The latch is read every tick and now
drives emergency cancel-all on fresh orders, at any age.

**Pre-registered stops:** any safety-control discrepancy, residual above 3.00
contracts, per-market deficit above $1.50, aggregate above $20, any failure of
emergency cancellation, the $5 daily halt without exception; and for worsening
residuals, cumulative settled residual ≤ −$2.50 or residual frequency above
the 71.4% baseline over ≥5 settled windows. **A negative P&L run alone is not
a stop** — a completed pair earns about $0.13 and that is noise.

**A matched-fill improvement will not be called profitability.** The all-time
ledger stands at **−$8.82910000**.
