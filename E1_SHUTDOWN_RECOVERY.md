# E1 shutdown and crash recovery — trace, patch, and GO/NO-GO

Nothing deployed, armed, restarted, or re-pointed. No Railway setting
changed. No order placed. The service remains pinned to `b88ec5eec` with
`restartPolicyType: NEVER`.

Work branch: `claude/e1-shutdown-recovery`, cut from the deployed commit
`b88ec5eecbb77151c07a10928b3687fd60c77cd4`. **Deliberately not the deployed
branch itself**, so no push can touch the pinned service.

## 1. Every shutdown path, traced

Production-path files: `analysis/l5/e1_cohort/runner.py`,
`analysis/l5/e1_cohort/pair_lifecycle.py`,
`analysis/l5/live_exec2/orders.py`,
`analysis/l5/e1_cohort/latch.py`,
`analysis/l5/e1_cohort/order_state.py`,
`scripts/l5_e1_cohort_run.py`, `scripts/l5_e1_prelive_validation.py`.

| Exit | Before | Cancelled owned orders? |
|---|---|---|
| **Normal, past cutoff** | `run_cohort` `past_cutoff` branch calls `lifecycle.deadman` | **Yes** — the only path that worked |
| **Normal, before cutoff** (verdict stop, attempt budget spent, iteration cap, deadline) | loop `break`/exhaust → straight to the attempt tape and `return` | **No** |
| **SIGTERM** (how Railway stops a container) | no handler anywhere in `e1_cohort` or `live_exec2`; default disposition terminates **without unwinding** | **No** — and no `finally` anywhere in the program would have run either |
| **SIGINT** | same | **No** |
| **Uncaught exception** | nothing wrapped the `while` loop, and `main` called `run_cohort` with no `try` | **No** |
| **SIGKILL / OOM / container hard stop** | process is not scheduled again | **No, and no in-process fix exists** |
| **Railway restart** | `restartPolicyType: NEVER` — nothing re-runs | **No** |

Verification of the signal claim:
`git grep -nE "signal\.|SIGTERM|SIGINT|atexit"` over
`analysis/l5/e1_cohort`, `analysis/l5/live_exec2` and both deployed
entrypoints returns **no matches** at `b88ec5eec` (the single hit is the
word "signal" in a test comment).

**This is the recorded 2026-10-08 incident.**
`scripts/l5_e1_cancel_outstanding.py`: E1 was disarmed and its process
stopped, *"which means ITS OWN DEADMAN NEVER RAN"* — twenty 0.13-share
orders left resting on `cpc-btc-updown-15m-2026-10-08-1230z`.

## 2. The patch: reachability only, mechanism untouched

**What was already correct and is not touched.** `pair_lifecycle.deadman`
cancels **by owned order id**, `POST /v1/order/{id}/cancel` through
`orders.cancel_exact`; membership in E1's own registry is the ownership
test; an unattributable order is reported and **left alone**; every id is
re-read individually afterwards because *"a cancel response is a claim and
the open-orders list is the fact"*; and it refuses outright when order
state cannot be established. The market-scoped `deadman_cancel_all` is
**not** on this path — the lifecycle docstring records why it was removed
(the PMUS account is shared: 1,100 activity rows, other desks at sizes like
16.21 and 23.49, so "market scope is not ownership scope").

*Correction to my previous report, which described the handover as using a
"market deadman": it does not. It is exact-id and has been since that
repair.*

### `analysis/l5/e1_cohort/shutdown.py` (new, 150 lines)

Turns SIGTERM and SIGINT into `ShutdownRequested`, raised in the main
thread, which makes the handover reachable on the unwind.

`ShutdownRequested` derives from **`BaseException`, not `Exception`** — the
sharpest detail in the patch. The iteration loop is full of
`except Exception` classifiers for venue and local faults; a shutdown
inheriting from `Exception` would be caught by the first of them, recorded
as a venue error, and **the loop would carry on placing orders during a
shutdown**. Pinned by
`test_shutdown_is_a_BaseException_so_the_loop_cannot_swallow_it`.

A second signal is recorded and **not** re-raised: re-raising from inside
the cancel sequence would abandon it half-finished.

`coverage()` reports `in_process_handler_covers_sigkill: False`,
`atexit_used: False`, and names SIGKILL / OOM_KILL / POWER_LOSS /
CONTAINER_HARD_STOP as **NOT_COVERED**, plus which modules do cover a hard
crash. No claim is made that this is crash protection.

### `analysis/l5/e1_cohort/runner.py` (+~170 lines, no reindentation)

The handover closure is installed in a module-level hook before the loop and
called from `main`'s `except`/`finally`. **Why the indirection:** the
alternative — wrapping the 450-line iteration loop in `try`/`finally` —
would reindent the entire body and bury a safety fix inside a whitespace
diff nobody can review. The closure holds the only things the handover
needs, and it is **cleared on every normal return**, so a hook still set
when `main` unwinds means precisely "a cohort stopped without completing
its handover".

Four exits now reach cancellation:

- `past_cutoff` — unchanged;
- normal exit before the cutoff — a new explicit call after the loop;
- `ShutdownRequested` — `main` cancels, emits `E1_SHUTDOWN`, returns **4**
  so an interrupted probe does not read as a clean run;
- any other `BaseException` — `main` cancels, emits `E1_ABNORMAL_EXIT`, and
  **re-raises the original failure untouched**, because that failure is the
  incident and must not be replaced.

The handover **re-reads venue state** rather than trusting the last
snapshot, since a shutdown can arrive long after the last iteration. If the
re-read fails it falls back to the last established view and **says so**
(`order_state_reread_failed`, `fell_back_to_last_established_view`), which
is safe because ownership comes from our registry, not the view: a stale
view cannot make us touch a stranger's order, the worst it can do is name an
id that has since gone terminal, and the per-id verification catches a wrong
view. With **no** view at all the deadman refuses.

`run_emergency_handover` **never raises**, for the same reason as above.

## 3. Hard-crash recovery, verified by simulated crash

`test_a_simulated_hard_crash_leaves_the_latch_and_the_tape_behind` injects
an exit that runs no handler, then asserts on **disk**, not on logs:

- the `O_CREAT|O_EXCL` latch slot is present, and carries
  `slug / side / price / qty / window / slot / pid` and
  `NOT_RELEASED_ON_FAILURE: true`;
- the submitted order id is recoverable from the append-only tape by role.

`test_a_restart_with_a_resting_order_refuses_to_submit` then starts a fresh
`run_cohort` with that order still resting:
`stopped_because == "STARTUP_RECONCILIATION_REFUSED"`, `may_submit False`,
`placement_count 0`, `latch_slots_consumed 0`, **zero POSTs**, and the
resting order is **not** cancelled — correctly, because cancelling on
ownership doubt is the operator's call.

`test_an_unreadable_venue_on_restart_also_refuses` pins the fail-open
direction shut: `STARTUP_RECONCILIATION_RAISED:OSError`,
`FAILS_CLOSED: True`, zero POSTs.

## 4. Fail-closed, confirmed on all three fronts

| Condition | Behaviour | Test |
|---|---|---|
| **Unreadable venue state** | deadman refuses with `ORDER_STATE_NOT_ESTABLISHED`, `FAILS_CLOSED: True`, and emits **no** `cancels` and **no** `all_cancelled` — no cancellation is claimed | `test_no_view_at_all_refuses_and_claims_no_cancellation` |
| **Failed cancellation** (venue answers 200, order stays open) | `all_cancelled False`, the id appears in `owned_still_open`, and `SHUTDOWN_HANDOVER_DID_NOT_CLEAR_OUR_OPEN_ORDERS` is a safety violation | `test_a_failed_cancel_is_a_violation_not_a_clean_shutdown` |
| **Refused handover** | `SHUTDOWN_HANDOVER_REFUSED` violation — a refusal is not a clean shutdown | same module |
| **Ambiguous submission** | unchanged and already correct: the latch slot is consumed *before* the POST and never released, so an unknown outcome is spent and never retried | `test_e1_restart_recovery.py` (pre-existing) |
| **Foreign order** | never cancelled; `foreign_untouched True` asserted under shutdown | `test_a_foreign_order_is_never_cancelled_by_the_shutdown` |

18 new tests, all passing.

## 6. Would `ALWAYS` restart invoke the right entrypoint? **No.**

Two independent reasons, and the restart policy is not the binding one:

1. **The startCommand override.** Railway runs
   `python3 -m scripts.l5_e1_prelive_validation`, which overrides the image
   `CMD` (`scripts.l5_e1_cohort_run`). That script **replicates**
   `reconcile_at_startup` as a read-only report (lines 136, 154, 203) and
   asserts `orders_cancelled: 0, latch_slots_consumed: 0, armed: False`. It
   never calls `run_cohort`. So `ALWAYS` would re-run a **validation
   report** in a loop. It would *surface* resting orders, which has
   monitoring value, but it cancels nothing.
2. **Even with the override removed**, the image `CMD` reaches
   `runner.main` → `run_cohort`, whose startup reconciliation would
   correctly **refuse** and return *before the loop* — which is before the
   emergency hook is installed. `run_cohort` states it "cancels nothing" at
   startup, by design. So that path does not cancel either.

**Changing the restart policy alone fixes nothing.** Recovery needs an
entrypoint whose job is cancellation: `scripts/l5_e1_cancel_by_id.py`
already exists in the image and already refuses any id E1's durable tape has
never seen. What is missing is something that *runs* it. That is a new
service or a restart-policy change plus a new startCommand — **not
authorized, so proposed only, not implemented.**

## 7. The 20 resting orders — independently verified, not assumed

I could not make an authenticated venue call from this session: no PMUS
credentials are present in this environment, and I did not read the
service's secrets to obtain any. So this rests on the committed,
venue-sourced record rather than a fresh read, and is labelled accordingly.

`analysis/l5/e1_cohort/FINANCIAL_RECONCILIATION_2026-10-08.md`, per order
id from E1's own tape:

- **1230z (the twenty "resting" orders): `ORDER_STATE_CANCELED` ×20**,
  `cumQuantity 0`, and appearing in **no** trade row — independent
  confirmation the cancel caught them before any fill. `CANCELED` is
  terminal and does not revert, so **they are not open.**
- Exposure confirmed zero three independent ways: `/v1/orders/open` →
  `{"orders": []}`; `/v1/portfolio/positions` → empty;
  `/v1/account/balances` → `openOrders 0, assetNotional 0,
  unsettledFunds 0, balanceReservation 0, marginRequirement 0`.

**A more serious finding in the same record, which the "20 resting orders"
framing understates.** The *previous* window, 1215z, had its own twenty —
and those **all FILLED**, inside 2.47 seconds, as maker on every fill
(`passiveExecution` ×20): a **2.60-share unhedged directional position**,
20× the intended single-leg size, with no complement submitted. It resolved
YES and paid **+$1.3260** (cost $1.2740, settlement $2.6000, venue-reported
commissions $0.0000, less any undisclosed settlement fee — the resolution
row's `fees` is `null`, not `0`, and is not asserted as zero).

That gain is **luck, not a control**: the identical sequence on a "No"
resolution loses the full $1.2740. It is not matched-pair economics, not
spread capture, not a rebate, and is not counted toward the $480/day target.

Label: **SETTLED, VENUE-CONFIRMED** per the source document; **not
independently re-read today.**

## 8. Q1 executor pricing-boundary patch — still isolated, risk closed

Branch `claude/pmus-q1-executor-pricing-boundary`, commit `0339f93d0`.
Undeployed, unarmed, and not on the deployed execution path.

The one risk the previous plan listed against itself is closed: `main` now
runs `armed()`, `has_credentials()`, `gate_asserted()` and the daily-loss
check **before** opening the websocket, so an armed-but-ungated invocation
no longer connects and then refuses. No gate moved or weakened —
`run_campaign` still performs all of its own checks and still owns every
refusal status; the predicates are read in `main` only to decide whether a
feed is needed. Three tests pin it, including that a fully authorized run
still opens the socket.
