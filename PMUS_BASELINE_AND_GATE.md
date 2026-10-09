# Clean baseline, gate wiring, and crash recovery

Live trading disarmed throughout. **No orders, no arming, no production
deployment, no risk-limit change.**

**Labels.** `[VENUE]` venue/platform-confirmed · `[CODE]` implemented and
tested · `[BLOCKED]` needs approval or does not exist.

---

## 1. Triage: all 35 failures cleared, no assertion weakened

Both root causes were **production defects**, not stale tests. The tests were
right and I changed code, not expectations.

### 34 × `test_q1_canary.py` — one line that was never written

`pmus_q1_executor.attach_ws_feed()` existed and was called from **nowhere in
the repository**. So `fresh_bbo` always saw `WS_FEED is None`, and the gate

> `WS_PRICING_REQUIRED: armed run with no authenticated WS feed attached`

fired on **every armed run**. The strategy loop read the authenticated
`LiveState` correctly; the executor's own pre-submit cross-check never got
connected to it. **No armed canary run could place an order, in any
configuration — in tests or in production.**

Fix `[CODE]`: a `_StateBackedFeed` that serves the executor's pricing boundary
from the same `LiveState`, reusing `LiveState.snapshot` so the staleness rule
keeps exactly one implementation, attached **before `preflight`** — because
`preflight` prices too, so attaching after it still halted.

A second ordering bug surfaced from that: a stale feed then came out as a
generic `WS_BBO_UNAVAILABLE` halt instead of the distinct `WS_STALE_REFUSED`
status, which is a real reporting contract two tests assert. `wait_ready` now
classifies staleness ahead of `preflight`, so the specific status survives.
**The gate itself is untouched and still refuses.**

### 1 × `test_only_the_oneshot_probe_arms_anything` — a second arming surface

`scripts/pmus_balance_admission_probe.py` called `tp.arm()` while **its own
docstring said "the transport is never armed"**. It only ever calls
`/v1/order/preview` through `_request`; `submit()`/`cancel()` — which the arm
gate guards — are never reached. The code contradicted its documented
contract and broke the repository's single-arming-surface invariant.

Fix `[CODE]`: removed the `arm()`/`disarm()` calls and report
`{"armed": false, "attempted": false}`. The invariant holds again: **only
`l5_pmus_live_oneshot` can arm anything.**

Baseline: `test_q1_canary.py` **77 passed**, `test_l5_live_oneshot.py`
**65 passed**, reproducible.

## 2. Guardian wired into the real entrypoint `[CODE]`

In `native_work_window`, **before the money path is constructed**:

1. `_build_guardian` opens the ownership store, defaulting to
   `$PMUS_Q1_STATE_DIR/guardian_ownership.json` — the same durable volume as
   the journal, so it survives the process. A store that cannot be opened
   raises `SubmissionBlocked`; an unusable path does not trade unprotected.
2. `GATE.open_for_business` binds id-less intents, then reconciles against
   `/v1/orders/open` and `/v1/order/{id}`.
3. `GATE.require_open_for_business` **raises** — deliberately not caught, so
   refusing to trade is the outcome.
4. `GATE.arm_shutdown` wires SIGTERM/SIGINT to cancel-and-verify.
5. `NMP.build(..., guardian=guardian)` threads it to `_post`, which persists
   an INTENT **before** `tp.submit_body`.

Both ambiguous submissions and unresolved venue orders block: a lost ack with
two matching candidates is `AMBIGUOUS`, with none is `UNMATCHED`, an unknown
live order is `UNKNOWN_LIVE_ORDER`, and all three set uncertainty that never
decays.

## 3. Crash recovery: the comparison, and what I did not change

| | ALWAYS-restart | separate scheduled reconciler |
|---|---|---|
| SIGKILL / OOM | container restarts; next process runs the mandatory gate and reconciles **automatically** | reconciles at the next tick; exposure lasts up to one interval |
| container failure / host loss | restart may be on a new host; still reconciles | unaffected — **does not share the trading process's fate** |
| lost acknowledgment | same process identity, store on the same volume, `reconcile_unbound` binds or blocks | same, provided it mounts the same volume |
| failure mode | a crash-loop re-reconciles repeatedly (idempotent, but burns venue reads) | **a dead reconciler is silent** — no recovery and no alarm |
| blast radius | the thing that crashed is the thing that recovers | independent, but needs its own credentials and volume mount |

`[VENUE]` Current facts: `l5-live-probe-oneshot` (which ran R4/R5/R6) is
`restartPolicyType: NEVER` — **a hard kill is never restarted, which is
exactly why R6's orphan sat 26 minutes**. `pmus-readonly-recovery` is also
`NEVER`, has **no volume mounted**, and is triggered by `RECOVERY_ORDER_ID`
env vars: a manual tool, not a scheduler.

**Recommendation: ALWAYS-restart on the probe service, plus the mandatory
gate** — smallest safe change, no new service, and the recoverer cannot be
silently dead. The scheduled reconciler is the better *second* layer but needs
its own volume mount and credentials, i.e. a new service.

**I changed no deployment configuration.** The one-field change
(`restartPolicyType: NEVER → ALWAYS` on `84aa9999`) needs your approval.
Until then `[BLOCKED]` **live trading stays blocked**, as you instructed.

I am still not claiming in-process cleanup is crash protection: SIGTERM and
`finally` run; **SIGKILL, OOM, container hard-stop and power loss run
nothing**. `atexit` remains deliberately unused.

## 4. End-to-end evidence `[CODE]`

`tests/test_oneshot_guardian_endtoend.py`, mocked venue, 9 tests. The decisive
one — `test_a_blocked_gate_means_no_body_ever_reaches_the_transport` — asserts
the fake transport recorded **zero submissions and zero cancels** when the
gate refuses. Also covered: clean venue proceeds; unknown live order refuses;
a recorded order that filled after exit is booked then allowed; unreadable
`/v1/orders/open` refuses; an ambiguous lost ack refuses; the store defaults
onto the durable volume; an unusable store path raises rather than trading;
and the SIGTERM sweep cancels and verifies through the transport.

## 5. R6 economics preserved `[CODE]`

Frozen in `LIVE_CASES["R6"]` with source-accounted lines and asserted by three
tests:

| line | amount |
|---|---|
| fills cost | −36.50000 `[VENUE]` |
| settlement proceeds (40 UP × $1.00) | +40.00000 |
| net commission credits | +0.05040 |
| **total** | **+3.55040** `[VENUE]` cash anchor |

**$3.50000 settlement-only + $0.05040 credits = $3.55040 net**, unchanged.

## 6. October 7 probes — one incident, one fill, nothing material outstanding

`[VENUE]` **XR2 (10-06 23:00Z): FAILED.** `orders_submitted: 0`,
`stopped_at: AMBIGUOUS_SUBMISSION` on **HTTP 400**; the record states *"we do
not know whether an order rested. No retry is possible or permitted."* The
deadman ran because an order might exist: `cancel_all ok`,
`canceledOrderIds: []`. Consistent with no order, but **not proof**. The latch
was correctly not released.

`[VENUE]` **XR2W (10-07 02:00Z): one fill.** `CYKE4XSQ0YGW`, DOWN 0.01 @ own
0.67, post-only, `ORDER_STATE_NEW` for ~15.2 s then FILLED, rest 65.2 s,
**fees $0**, markouts +0.03…+0.09, liquidity role **`null`** (no
`isAggressor`), `settlement: observed: false`,
`final_net_usd: UNRESOLVED_UNTIL_CASH_CONFIRMED`, residual **0.01 shares**.

**Is anything unreconciled?** I searched that service's logs after 02:05 for
`CYKE4XSQ0YGW` and the `0200z` slug: **no follow-up record exists**, so the
position was never explicitly reconciled by us. Materially it is bounded at
**$0.01** (`MAX_POSSIBLE_LOSS_USD 0.0099`), the market expired long ago, and
the most recent authoritative account read `[VENUE]` — `l5-pmus-live`
preflight, 2026-10-09 17:30Z — shows `no_unexpected_open_orders: passed, "0"`
and `live_orders: 0`. **No open orders remain.** But the fact that a fill went
unreconciled in our own records is a small instance of exactly the gap the
guardian closes.

## 7. Smallest deployable patch, and the next test

**Patch — owning branch `claude/l5-pmus-economic-results-7c116e`:**

| file | change |
|---|---|
| `src/pmus_q1_canary.py` | `_StateBackedFeed` + attach before preflight + staleness classified first |
| `scripts/pmus_balance_admission_probe.py` | drop `arm()`/`disarm()` |
| `scripts/l5_pmus_live_oneshot.py` | mandatory gate, shutdown sweep, venue read helpers |
| `src/pmus_native_money_path.py` | `guardian=` on `__init__` and `build`; INTENT before POST |
| `src/pmus_order_guardian.py`, `src/pmus_guardian_gate.py` | new |
| 4 test files | new |

Guardian is optional by construction; with no store path configured every
existing caller behaves exactly as before.

**Next minimum-size live test — unchanged design, now with a real gate:**
reuse `LIVE-L5-EXEC-1-XR2W`. Vary `max_age_s ∈ {0.25, 1.0, 3.0}`, one arm per
window, placement fixed at `offset_ticks_from_touch = 0`, minutes 0–13,
**0.01 contracts** ($0.01 worst case per order), **one concurrent order**, 30
orders per arm / 90 total, **$1.00 total realized-loss limit**, filled
residuals left to settle. Preregistered: rejection rate rises with book age;
**if it is flat across buckets the arrival-book model is wrong and
`ARRIVAL_LAG_S` must be reverted.**

## Remaining blockers

1. `[BLOCKED]` **Crash recovery needs the one-field restart-policy change**
   (§3). Live trading blocked until then.
2. `[BLOCKED]` **The patch is not deployed.** Needs your approval; the probe
   service is pinned to a commit on a different branch.
3. `[VENUE]` **Maker role unprovable** — no `isAggressor`.
4. `[VENUE]` **Queue position unobservable.**
5. `[VENUE]` **No client order id**, so lost-ack identity stays heuristic.
6. `[BLOCKED]` **XR2's ambiguous submission is unresolvable retrospectively** —
   HTTP 400 with no order id means there is nothing to query.
