# Stage B2 — authorized campaign record (2026-10-31)

Owner authorization received 2026-10-31. **Arming is gated on the verification
below; nothing is armed until every row passes.**

## The authorization actually minted

| field | value |
|---|---|
| document sha256 | `f721aefc749cb8f101514d895fde45864f48812c6c41d9181cd63f51f5dc417c` |
| nonce | `camp-BpS2…` (in `L5_CAMPAIGN_APPROVAL` only; never logged) |
| series | `cpc-btc-updown-15m-` |
| issued_utc | 2026-10-31T12:21:38Z |
| **starts_utc** | **2026-10-31T13:00:00Z** |
| **expires_utc** | **2026-10-31T15:15:01Z** |
| campaign span | 2.89h vs the 4h `MAX_CAMPAIGN_S` ceiling |
| max_windows | **8** |
| max_total_exposure_usd | **20** |
| max_daily_loss_usd | **5** |
| max_live_orders | 1 |
| pair_cost_ceiling | 0.96 |
| OPEN / COMPLETE / REPLACE / total | **32 / 32 / 8 / 56** |
| completion_reserve_submissions | 2 |
| gate verdict | `gate_says_approved: true`, `gate_why: null` |
| **clamped** | **nothing** — exposure, daily loss, windows and expiry all `false` |

`starts_utc` is carried for the first time. The gate has refused
`WINDOW_BEFORE_AUTHORIZED_START` since the 0145z refusal, but the generator
never emitted the field, so no document could set it until `09cc4a11`.

## The eight authorized windows (2026-10-31 UTC)

| # | start | slug |
|---|---|---|
| 1 | 13:00z | `cpc-btc-updown-15m-2026-10-31-1300z` |
| 2 | 13:15z | `cpc-btc-updown-15m-2026-10-31-1315z` |
| 3 | 13:30z | `cpc-btc-updown-15m-2026-10-31-1330z` |
| 4 | 13:45z | `cpc-btc-updown-15m-2026-10-31-1345z` |
| 5 | 14:00z | `cpc-btc-updown-15m-2026-10-31-1400z` |
| 6 | 14:15z | `cpc-btc-updown-15m-2026-10-31-1415z` |
| 7 | 14:30z | `cpc-btc-updown-15m-2026-10-31-1430z` |
| 8 | 14:45z | `cpc-btc-updown-15m-2026-10-31-1445z` |

Last window closes 15:00z; the approval expires 15:15:01z, giving one window
of settlement slack and refusing a ninth window two ways (window budget and
expiry).

## Preflight, taken before the variable write

| check | result |
|---|---|
| authoritative cash | **$342.56275**, age 0.0s, source `/v1/account/balances` |
| cash tie-out | $341.21875 baseline + $1.3440 Stage B1 = $342.56275 **exact** |
| `cash_deployed` | `[]` |
| `committed_usd` | `{}` |
| venue open orders | **0** — confirmed independently by `l5-halt-watchdog` |
| live_orders (engine) | 0 |
| real residual inventory | none |
| watchdog | sweeping ~20s, `cancel_enabled true`, `read_error null`, `halt false` |
| `unresolved_accounting` | count 0, `blocks_live_authorization false` |
| conflicting authorization | none — Stage B1 doc `f4cfbbb4…` reads `approval_stale`, `permits_this_window: null` |
| fresh class budget | a new nonce gets its own tickets (`cls1.<base>.camp.<digest>.…`), so Stage B1's spend cannot carry over |

**One preflight item needed care and is recorded because it looked alarming.**
The 11:47 heartbeat showed `inventory {"no":1,"residual":-1}`. That is **not**
venue inventory: it sits under `L5_SHADOW_TWO_SIDED` → `stranded`, labelled
"MODELLED by the queue-aware model from venue settlement prices. NOT realized,
NOT filled, NOT submitted." Real venue state is flat.

## Engine identity — unchanged

The service deploys from this branch, so a variable write redeploys from
branch HEAD rather than the validated commit. That is only safe because the
engine is byte-identical:

```
git rev-parse ff00d287^{tree}:src  = bbb4d031b8f0cae984c19f6856418069ea46ace3
git rev-parse 9f5a3b4c^{tree}:src  = bbb4d031b8f0cae984c19f6856418069ea46ace3   IDENTICAL
deploy/ tree                        IDENTICAL
```

Everything committed since `ff00d287` is analysis documents plus
`tools/make_campaign_approval.py`, which runs **outside** the service by
design (`EnvApprovalStore.put` refuses to let the executor write its own
authorization). No `src/` or `deploy/` file changed, so the DOWN-completion
fix `69b19625` and every risk setting are the same code that ran Stage B1.

## Owner decisions applied

1. **The five-window negative-P&L automatic stop is removed.** A negative
   matched run is now cause to **investigate while the campaign continues**.
   Halt only on a confirmed defect or an existing risk trigger.
2. Submission budgets OPEN 32 / COMPLETE 32 / REPLACE 8 as authorized.

## A correction I made to my own proposal

I had proposed a flat backstop of 72, reasoning that
`submission_budget_refusal` refuses on `len(spent) >= cap` in both claim paths
regardless of class and would therefore refuse a COMPLETE and strand exposure.
That reading was incomplete: the call site guards it with
`total_backstop_binds(reducing)`, which returns `not reducing`, so a
risk-reducing order can never be refused by the backstop. The value is
therefore **56** — below the class sum (72), preserving the convention that
OPEN and COMPLETE cannot both max out, while bounding only OPEN + REPLACE
(~35 expected across eight windows against a cap of 40).

## Automatic stops in force

| trigger | mechanism |
|---|---|
| $5 daily settled loss | `day_realized_line()` halt |
| approval expiry 15:15:01Z | `approval_gate()` → `APPROVAL_NOT_VALID` |
| 8 windows spent | window budget → no ninth arm |
| window before 13:00:00Z | `WINDOW_BEFORE_AUTHORIZED_START` |
| $20 aggregate exposure / $1.50 per-market deficit | risk gate |
| stale or overdue orders | independent `l5-halt-watchdog`, exact-owned-ID cancellation |
| crash or completion | fails closed; no automatic relaunch |

## Deployment verification (12:30:19Z heartbeat, after the variable write)

The running process recognizes the authorization exactly:

| field | running value | expected | |
|---|---|---|---|
| `approval_state` | `approved` | approved | ✅ |
| `document_sha256` | `f721aefc749cb8f101514d895fde45864f48812c6c41d9181cd63f51f5dc417c` | same | ✅ |
| `max_windows` | 8 | 8 | ✅ |
| `windows_spent` / `remaining` | 0 / 8 | 0 / 8 | ✅ |
| `max_submissions` | 56 | 56 | ✅ |
| OPEN | max 32, spent 0, remaining 32 | 32 | ✅ |
| COMPLETE | max 32, spent 0, remaining 32 | 32 | ✅ |
| REPLACE | max 8, spent 0, remaining 8 | 8 | ✅ |
| `completion_reserve.may_open` | true | true | ✅ |
| `blocks_advancement` | false | false | ✅ |
| `exact_market_binding` | null | null | ✅ |
| `armed` / `owner_arm_token` | false / false | disarmed | ✅ |

The fresh class budget is confirmed: Stage B1's 104/81/23 did not carry over,
because a new nonce gets its own `cls1.<base>.camp.<digest>.…` tickets.

## The 15:00Z boundary — the owner's check found a real hole

**`permits_this_window: true` was reported for the 1230z window**, which is
before `starts_utc` 13:00:00Z. That turned out to be a reporting artifact, not
an enforcement failure — but chasing it exposed a genuine gap.

`permits_this_window` is **budget-only**:

```python
out["permits_this_window"] = bool(slug in spent_w or out["windows_remaining"] > 0)
```

It never calls `authorize_window`, so it applies neither `starts_utc` nor
anything else; and the heartbeat's scope report copies only `max_windows`,
`series`, `expires_utc` and `clamped` — **never `starts_utc`**. The comment
above it calls itself "THE CANONICAL GATE … the verdict try_arm actually
enforces", and for this field that claim is false.

Running the **deployed** gate code against the **deployed** document:

```
1215z  WINDOW_BEFORE_AUTHORIZED_START      1300z  ok
1230z  WINDOW_BEFORE_AUTHORIZED_START      1315z  ok
1245z  WINDOW_BEFORE_AUTHORIZED_START      1430z  ok
                                           1445z  ok
                                           1500z  ok   <-- THE HOLE
                                           1515z  ok   <-- and beyond
```

and at 15:00:20, when a 1500z window would arm, `1500z ok=True`.

**So the authorization document bounds the early side and not the late side.**
`expires_utc` is compared against `now`, not against the window's start, so at
15:00:20 an expiry of 15:15:01Z is still in the future and
`CAMPAIGN_EXPIRED` does not fire. The only thing that would have stopped the
1500z window is the 8-window budget — which has room the moment **any** one of
the eight authorized windows fails to arm. That is precisely the silent
schedule shift the owner forbade, and it could not be proven safe.

### The fix, applied before submissions were enabled

`L5_LIVE_AUTH_NOT_AFTER = 2026-10-31T14:58:00+00:00` — the owner's absolute
deadline, which `authorize_arming` evaluates at step 8 and which bounds *when
a window may arm at all*, independently of the campaign budget:

```
1445z arms        14:45:20   permitted
1445z last submit 14:57:00   permitted
1500z arms        15:00:20   AUTHORIZATION_EXPIRED  (refused)
```

14:58:00Z is chosen because it is strictly after every legitimate submission
of the eighth window — entry cutoff 600s → 14:55:00, complete cutoff 720s →
14:57:00, cancels from 14:55:00 — and strictly before 15:00:00, so the 1500z
window is excluded with two minutes of margin rather than by a ~20-second
race against arm latency. It is a variable, not a code change, and it only
ever refuses more. Cancellation is never gated by it, and `expires_utc` stays
at 15:15:01Z so settlement and reconciliation of the 1445z window still have
slack after the last submission deadline.

Four independent bounds now hold the schedule:

| bound | mechanism | stops |
|---|---|---|
| `starts_utc` 13:00:00Z | `WINDOW_BEFORE_AUTHORIZED_START` | any window before the first |
| `max_windows` 8 | window ledger | a ninth window |
| **`NOT_AFTER` 14:58:00Z** | **`AUTHORIZATION_EXPIRED` at arming** | **any window after the eighth, budget or no budget** |
| `expires_utc` 15:15:01Z | `CAMPAIGN_EXPIRED` | the campaign outliving its settlement slack |

## Armed — `L5_AUTHORIZATION_PREFLIGHT` at 12:38:27Z

`ok: true`, `fail: []`, `unknown: []`, `reserved_nothing: true` (read-only).

| check | verdict | evidence |
|---|---|---|
| `shadow_lock` | **PASS** | `engaged: false` — the structural lock is released |
| `owner_arm_token` | PASS | all three arm variables agree |
| `absolute_deadline` | **PASS** | `state OPEN`, `expires_in_s 8372.8` → from 12:38:27 that is **exactly 14:58:00Z** |
| `campaign_approval` | PASS | `approved`, `max_windows 8`, `approved_exposure "20"`, `approved_daily_loss "5"`, `document_sha256 f721aefc…` |
| `window_budget_env` | PASS | `max_windows_env 8` |
| `halt_gate` | PASS | `permitted: true` |
| `residual_ceiling` | PASS | `k 2`, absolute cap 3 contracts, `which_binds ABSOLUTE`, outer bound 10 |
| `market_series`, `claim_ttl_s` (900s), `role_binding`, `session_id` | PASS | |

Three rows read `NOT_EVALUATED` — `window_bounds`, `order_state_and_reconciliation`,
`claim_held_and_scope_digest` — because they need a live market slug, an
authoritative venue read, or are created inside `authorize_arming` itself.
They are correctly **not** counted as PASS.

Arm write sent 12:37:17Z, deployment `31d90751` SUCCESS, engine tree still
byte-identical to `ff00d287` (`src` and `deploy` both). First window opens
13:00:00Z, so no window was missed and the schedule stands unshifted.
