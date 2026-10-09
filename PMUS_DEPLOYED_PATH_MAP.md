# The deployed production path, mapped from source

Read-only mapping. Nothing deployed, armed, restarted, or re-pointed.

Service: Railway `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`)
Volume: `l5-live-probe-state` at `/state/pmus-ws`, 50 GB
Branch: `claude/august-l5-forensic-replay-788cml`, pinned `b88ec5eecbb77151c07a10928b3687fd60c77cd4`
Restart policy: `NEVER`

All line numbers below are at that commit.

## 1. Entrypoint, Dockerfile, submission path, cancellation path

### Headline correction to the prior working assumption

**The deployed service does not run the `pmus_native_money_path` /
`pmus_q1_executor` / `scripts/l5_pmus_live_oneshot.py` path at all.**
It runs the **E1 cohort** path under `analysis/l5/e1_cohort/`.

Consequence: the `pmus_order_guardian` / `pmus_guardian_gate` work is on a
*different execution path* from the deployed service. Porting it is not the
minimum change for this service — see section 3.

### Dockerfile

`deploy/l5_e1_cohort/Dockerfile` (confirmed; **not** the lock5 Dockerfile).
`python:3.11-slim`, 52 `COPY` lines, `cryptography==49.0.0 websockets==17.2`.

Image `CMD` (line 277): `python3 -m scripts.l5_e1_cohort_run`
Railway `startCommand`: `python3 -m scripts.l5_e1_prelive_validation`

The Railway `startCommand` **overrides** the image `CMD`, so the entrypoint
that actually runs is `scripts.l5_e1_prelive_validation`. Both files are in
the image (line 120 and line 190), so either can run.

### Entrypoint A — what actually runs

`scripts/l5_e1_prelive_validation.py` (413 lines). Imports from `src/` are a
single module: line 46 `from src import pmus_auth as A`. Everything else is
`analysis.l5.e1_cohort.*`. Its own docstring: *"E1 STAYS DISARMED. This
script never reads an arming key, never consumes a latch slot."* Its closing
record asserts `armed: False, orders_submitted: 0, orders_cancelled: 0,
latch_slots_consumed: 0`.

**This entrypoint does not reach a submission path.** It replicates the
armed startup call (line 117) and reports which refusals fire; the expected
set includes `E1_DISARMED` (line 383).

### Entrypoint B — the money path, present but not the startCommand

`scripts/l5_e1_cohort_run.py` (31 lines) → `analysis.l5.e1_cohort.runner.main`.

**Submission path:**

```
runner.run_cohort
  -> eligible()                      analysis/l5/e1_cohort/runner.py:452
       arming.armed_env_summary()     -> "E1_DISARMED" when unarmed (:459-460)
  -> e1_preflight()                  :631   (handed to orders.submit_one(checks=...))
  -> submit_first_leg()              :748
  -> _submit_once()                  :654
       O.order_body            -> src.l5_order_body.build
       POST /v1/order/preview  -> O.PREVIEW_PATH
       G.require_echo          -> src.l5_preview_gate
       consume_latch()         -> e1_cohort.latch.consume_attempt   <-- durable intent
       POST /v1/orders         -> O.SUBMIT_PATH
  -> submit_complement()             :969   (own latch slot per tranche)
```

Arming requires all of `PMUS_E1_LIVE=YES`, a fingerprint pin, and
`PMUS_E1_ARM == PMUS_EXEC1_SESSION_ID`-equivalent session id
(`analysis/l5/e1_cohort/arming.py:99-105`). E1 deliberately does **not** read
`PMUS_Q1_STATE_DIR` or any Q1 key, and `assert_no_foreign_executor_authorization`
(arming.py:71) refuses if foreign executor keys are present.

**Cancellation path:**

```
analysis/l5/live_exec2/orders.py
  cancel_exact(t, order_id, slug)   :398  -> POST /v1/order/{id}/cancel
  deadman_cancel_all(t, slug)       :403  -> POST /v1/orders/open/cancel  {"slugs":[slug]}
```
Driven from `pair_lifecycle.deadman`, invoked in `run_cohort` at
`runner.py:1492-1519` when `past_cutoff and not lifecycle.deadman_result`,
with ownership scoping — a cancel that removes an order E1 does not own is
recorded as `HANDOVER_CANCEL_TOUCHED_ORDERS_WE_DO_NOT_OWN` (:1519).
`BoundedOrderTransport` permits only the four routes.

Out-of-process operator recovery, already in the image:
`scripts/l5_e1_cancel_outstanding.py` (market-scoped deadman + re-read of
`GET /v1/orders/open` to verify) and `scripts/l5_e1_cancel_by_id.py`
(exact-id, and refuses any id the durable tape has never seen — the shared
account makes market-scoped cancel unsafe for routine use).

## 2. The complete chain, link by link

| Link | Status on the deployed branch | Evidence |
|---|---|---|
| Authenticated pricing | **Present on this path.** `pmus_provider` / `pmus_book` supply the snapshot; `e8_book_executable` (runner.py:377) and `_ws_ok` (:322) refuse an unexecutable or stale book. This is a different provider from the `pmus_q1_executor` boundary that is broken. | runner.py:322, :377, :452 |
| Preflight | **Present.** `e1_preflight` (:631) builds the check set passed to the submit helper; preview + `require_echo` run strictly before the latch. | runner.py:631, :686-720 |
| Persistent INTENT before submit | **Present, and stronger than the guardian's journal.** `latch.consume_attempt` creates one named slot per attempt with `O_CREAT\|O_EXCL`, writes `{slug, side, price, qty, window, slot, role, tranche, pid, RAILWAY_DEPLOYMENT_ID, consumed_unix}`, flushes and `fsync`s, immediately before the POST, and **never releases on failure**. A half-written slot still refuses to submit (`LatchUnavailable`). Bound is on *files*, so no read-then-write race can inflate it. | latch.py:237-253, :256-269, :272-305 |
| Submit | **Present.** Single POST, never retried. | runner.py:724-738 |
| Acknowledgment | **Present.** `order_id` read from `id` / `orderId` / `order.id`; returns `order_id: None` when absent rather than inventing one. | runner.py:739 |
| Ambiguous submission | **Present and correctly typed.** Any failure at or after the POST raises `O.SubmissionAmbiguous` carrying the latch record; a pre-POST venue failure raises `O.PreviewRejected`, which leaves the latch untouched. Local faults are separated from venue errors by *re-running the signing contract* (`LF.classify_preview_exception`), not by exception class. | runner.py:724-738, :700-712 |
| Reconciliation | **Present and fails closed.** `run_cohort` refuses to open the first window until `OST.reconcile_at_startup` returns `may_submit` (:1340-1380). A raise becomes `STARTUP_RECONCILIATION_RAISED:<type>` with `FAILS_CLOSED: True`. Pre-existing outstanding orders or an open residual stop the cohort and are **not** inherited. Order state is venue-sourced only, with `MAX_ORDER_STATE_AGE_S = 5.0`, and unreadable state is `ORDER_STATE_UNKNOWN_FAIL_CLOSED`, never zero. | runner.py:1340-1380; order_state.py:39, :254, :295, :646 |
| Registry restoration | **Present.** `order_registry.load_from_tape` rebuilds owned ids from the append-only tape on the volume; `lifecycle.record_first_leg` re-seeds them so a restart complements legs it already owns instead of disowning them (:1382-1398). | order_registry.py:99, :177; runner.py:1393-1398 |
| Recovery / shutdown cancellation | **THE GAP. See below.** | |

### The one real gap

**There is no signal handler and no `finally` around the handover.**

- `git grep -nE "signal\.|SIGTERM|SIGINT|atexit"` over
  `analysis/l5/e1_cohort`, `analysis/l5/live_exec2`, and both deployed
  entrypoints returns **no matches** (the single hit is the word "signal"
  in a test comment).
- The handover deadman is reached only from inside the iteration loop, on
  the `past_cutoff` branch (runner.py:1492). It is not in a `finally`, so an
  exception escaping the loop, a `SIGTERM`, or a hard stop leaves resting
  orders uncancelled. Python's default `SIGTERM` disposition terminates
  without unwinding, so even adding a `finally` would not cover `SIGTERM`
  without a handler.
- `restartPolicyType: NEVER` means nothing re-runs after the process dies,
  so there is no in-container actor to cancel afterwards either.

**This is not hypothetical.** `scripts/l5_e1_cancel_outstanding.py`'s own
docstring records that on `cpc-btc-updown-15m-2026-10-08-1230z` E1 was
disarmed and its process stopped, *"which means ITS OWN DEADMAN NEVER RAN"*,
leaving 20 resting 0.13-share NO orders, all `ORDER_STATE_NEW`, cancelled
afterwards by hand. Same failure shape as R6's post-exit fill.

A second, separate defect is recorded in the same docstring — a hard-coded
`our_outstanding_orders: 0` in `pmus_provider` meant the declared
one-outstanding-order rule *was never enforceable*, so the engine opened a
new first leg every iteration up to the 20/window cap. **That one is already
fixed at the deployed commit** (`pmus_provider.py:579`, see section 3 item 3);
the deadman gap is not.

## 3. Minimum changes required on the currently deployed branch

Stated as what is *missing*, now that the chain above is mapped. Four items,
all inside `analysis/l5/e1_cohort/` plus `deploy/l5_e1_cohort/Dockerfile`.
**No guardian port. No branch switch.**

1. **`SIGTERM`/`SIGINT` handler that runs the handover.** Install in
   `scripts/l5_e1_cohort_run.py` before `runner.main`, raising a dedicated
   exception so the existing handover code path runs. Must be paired with (2),
   because a handler alone does not help if the handover is not reachable from
   the unwind path.
   *Explicitly not claimed:* this does **not** protect against `SIGKILL`,
   OOM, power loss, or a container hard stop.
2. **Wrap the iteration loop so the handover deadman runs on every exit**,
   not only on `past_cutoff`. The cancel mechanism and its ownership scoping
   already exist and are frozen; only the reachability changes.
3. ~~Fix `pmus_provider`'s hard-coded `our_outstanding_orders: 0`.~~
   **Already repaired at the deployed commit — no change needed.**
   `pmus_provider.py:579` now publishes
   `OS.outstanding_for_snapshot(ostate)` from venue state, with
   `OS.UNKNOWN_OUTSTANDING` as the fail-closed sentinel when the view cannot
   be established (:270, :288). `OUTSTANDING_ORDER_INVARIANT_REPAIR.md`
   records the fix (fingerprint `5e15bdc0…`, invariants 191 → 212). The
   docstring in `l5_e1_cancel_outstanding.py` describes the *pre-repair*
   state that produced the 20 resting orders; it is now stale as a statement
   about this commit.
4. **An out-of-process recovery actor.** `restartPolicyType: NEVER` plus a
   single container means nothing cancels after a hard crash. The cancel
   tooling already exists in the image; what is missing is something that
   *runs* it. That requires either a restart-policy change or a second
   service, **neither of which is authorized**, so this is recorded as an
   open requirement and not implemented.

Items 1–2 are code-only and do not change the restart policy, arming, or any
other service. None of them is proposed for deployment in this report.

## 6. Deployment closure, verified against the right Dockerfile

Checked against `deploy/l5_e1_cohort/Dockerfile`:

- `scripts/l5_e1_prelive_validation.py` — **copied** (line 190). The
  Railway `startCommand` resolves inside the image.
- `scripts/l5_e1_cohort_run.py` — **copied** (line 120).
- `analysis/l5/e1_cohort/` — **copied as a whole directory** (line 112), so
  every module on the submission path is closed, including future files.
- `analysis/l5/live_exec2/` — **copied whole** (line 114), so `orders.py`,
  `cancel_exact`, `deadman_cancel_all` and the latch spec are closed.
- `src/pmus_auth.py`, `src/l5_order_body.py`, `src/l5_preview_gate.py` —
  copied (lines 68-71). The validation entrypoint's only `src` import is
  satisfied.
- `src/pmus_order_guardian.py`, `src/pmus_guardian_gate.py` — **absent**,
  as expected. They are not on this path; their absence is not a closure
  failure for this service.
- The image already self-checks closure at build time with three
  `RUN python3 -c "import scripts.l5_e1_cohort_run..."` steps (lines 225,
  254, 268), and `analysis/l5/e1_cohort/test_dockerfile_closure.py` asserts
  it in the suite.

**Closure verdict: the deployed image is closed for both entrypoints as they
stand.** Changes 1–2 above stay closed too, since they touch only
whole-directory-copied trees and an already-copied script.
