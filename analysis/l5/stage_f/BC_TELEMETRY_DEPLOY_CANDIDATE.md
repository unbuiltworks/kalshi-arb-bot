# Deploy candidate `a1981165`: review, parity, preflight — and one blocker

**Date:** 2026-11-02T00:20Z · **Executor: disarmed** · **Nothing deployed**

---

## 1–2. Review and confirmation: observational only, parity exact

`4d7f510d` touches four files; **only one is in the executor image**
(`tests/` is not COPYed — verified against the Dockerfile at the running SHA).

So I built a candidate based **directly on the running SHA**, not on my branch
tip, because 27 commits separate them and several touch money-path files:

| | |
|---|---|
| candidate | **`a1981165`** (local branch `deploy-bc-telemetry`) |
| base | **`4d52345d`** — the commit actually running |
| image-file diff | **`src/l5_pmus_live_runner.py` only, +99/−3** |
| imports on the base | clean; `_bc_capture` present |

### Observational only — proven on the AST, not by inspection

| property | result |
|---|---|
| both `self.*.handle(...)` calls in `maybe_book_completion` | **argument-identical** to the running SHA |
| `raise` statements in that method | **3 at the running SHA, 3 here, identical** |
| `_bc_capture` signature | takes `result` only — **no `self`** |
| `_bc_capture` calls | `bool`, `get`, `isinstance`, `str`, `type` — nothing else |
| price / size / qty / limit / gate / ceiling / cancellation | **untouched** |

The three deleted lines are the *opening* lines of the two placement calls,
rewrapped; their arguments are byte-identical. Only the return value is now
captured. A refusal can never be reported as a submission.

### Deployment parity: byte-identical failing-id set

| | passed | failed | errors | failing-id sha256 |
|---|---|---|---|---|
| deployed tree baseline | — | 54 | 26 | **`eb672d14b4ba`** |
| candidate `a1981165` | 14,860 | **54** | **26** | **`eb672d14b4ba`** |

80 failing ids both sides, identical set. The 14,860 includes the **14 new
contract tests**.

## 3. Deploy — BLOCKED. One authorization needed.

Two facts make this not a push:

1. **The service is pinned.** `source.commitSha = 4aae0894…`, and a commit pin
   **suppresses branch-push triggers entirely**. Touching
   `deploy/l5_pmus_live/DEPLOY_TRIGGER` will build nothing. The previous
   deploy needed `connect-service-source` to repin — a service-config change
   you authorized explicitly for `4aae0894`.
2. **The narrow commit is not on an authorized branch.** `a1981165` is based
   on `4d52345d`, not on `claude/compassionate-allen-wic64u`'s tip, so making
   it reachable means pushing a **new branch** — which my standing instruction
   forbids without your permission.

**What I will NOT do on my own judgement:** pin to `4d7f510d` instead. That
would also deploy four other source files from the intervening commits:

| file | what else would ship |
|---|---|
| `l5_live_authorization.py` | a **new arming refusal** (the promotion gate — inert *only* because `MANDATE_PUBKEYS` is empty), a submit-pause switch (inert unless set), the strategy fingerprint in the claim |
| `l5_runtime_config_verify.py` | 11 observed-only rows |
| `l5_two_sided_probe.py` | strategy fingerprint, `binding_completion_deadline_s` (read-only) |
| `deploy/l5_pmus_live/Dockerfile` | one `COPY src/l5_promotion_gate.py` |

Each is individually inert or read-only. **But shipping a new arming refusal
is not "narrowly scoped instrumentation preserving the current trading
policy", and I am not going to widen the deploy by my own reading of
"inert".**

**Exact action needed:** permission to (a) push branch `deploy-bc-telemetry`
(commit `a1981165`), and (b) repin the service to it via
`connect-service-source`. That is the same shape you authorized for
`4aae0894`.

## 4. Verify emission — not possible until deployed.

## 5. Preflight, re-run fresh (2026-11-02T00:12–00:16Z)

| gate | status | evidence |
|---|---|---|
| watchdog | **PASS** | 00:16:26Z, `cancel_enabled true`, `open_orders 0`, `overdue/not_ours/still_open/unverified` all empty, `read_error null`, `emergency_cancel false` |
| reconciliation | **PASS** | `reconciled true`, `uncertainty_cleared true`, `ALERT null`, `authority VENUE` |
| venue orders | **PASS** | `open_orders_at_venue 0`, `pending_terminal_total 0`, `released_unacked []`, `adopted []`, `quarantined []`, `risk_floor_usd {}` |
| **loss gates** | **PASS** | **`utc_day: 2026-11-02`, `utc_day_realized_usd: 0`** — the daily gate **reset at UTC midnight**, full **$5** headroom; cumulative $8.9046 vs binding **$18.6325** ($9.73 room) |
| disarmed | **PASS** | `armed false`, `halt_latch CLEAR` |
| venue health | **PASS** | 1,994 requests, **0 × HTTP 429**, no cooloff |
| **authorization** | **FAIL** | **`approval_state: approval_stale`** |

The midnight rollover is a live confirmation of the reset semantics I reported
on 2026-11-01: `realized_current_utc_day()` reset to **0** while the
`realized_today` telemetry scalar stayed at **−8.9046**, which is exactly the
lifetime-vs-daily distinction, now observed rather than argued.

## 6. Fresh approval — owner-only, unchanged

`EnvApprovalStore.put` raises *"the executor may not write its own approval"*.
Install on `l5-pmus-live` as `L5_CAMPAIGN_APPROVAL`:

```json
{"approved": true, "nonce": "<>=8 chars, single-use>",
 "series": "cpc-btc-updown-15m-",
 "issued_utc": "<ISO8601, under 1h old when read>",
 "expires_utc": "<ISO8601, clamps to issued+4h>",
 "starts_utc": "<the FIRST window's start>",
 "max_windows": 4, "max_total_exposure_usd": "20", "max_daily_loss_usd": "5"}
```

## What the campaign will then measure

Once `a1981165` is running, every window with unmatched inventory yields the
seven figures you asked for, from the tape rather than from inference:
profitable completions identified (`action: COMPLETE` + `pair_own_cost`),
executable at depth (the paired `refused` naming
`COMPLEMENT_DEPTH_INSUFFICIENT` or not), orders submitted
(`outcome.submitted`), fills (lifecycle `cum_qty`), why the rest failed (the
named refusal), and matched / residual / total realized P&L at settlement.

On the last campaign's rate — 145 decisions across 5 windows — four windows
should produce several hundred paired records, which is ample to rank the
causes.

**Nothing armed, nothing deployed, no risk control or trading policy changed,
0.96 ceiling intact, no taker crossing, no DCA, no new service, $0 cost.**
