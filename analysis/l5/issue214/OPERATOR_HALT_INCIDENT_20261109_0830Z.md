# 2026-11-09 08:30Z — approved window produced zero orders

**Outcome: failed closed. Zero orders, zero fills, zero exposure, window not
consumed, settled realized P&L unchanged at −$15.6109.** No money was at
risk at any point. What was lost was the window, and the reason it was lost
is a readiness-reporting defect.

---

## 1. What happened, from production

Deployment `41defbb9-19e1-46b4-8ea8-b3b912e0987c`, SHA `68553c7` (a docs
commit on top of `543f4201`), ran the exact approved market
`cpc-btc-updown-15m-2026-11-09-0830z`.

From the 08:45:34Z heartbeat and the window's decision rows:

| field | value |
|---|---|
| `campaign.approval_state` | **`approved`** |
| `campaign.document_sha256` | **`6581138a…`** — the owner's published document, digest as predicted |
| `campaign.windows_spent` / `windows_remaining` | **0 / 1** |
| `campaign.submissions_spent` | **0** |
| `submission_gate.shadow_lock` | **`false`** — the owner had cleared it |
| `submission_gate.halt_latch` | **`CLEAR`** — the independent watchdog was fine |
| `submission_gate.owner_arm_token` | `true` |
| `submission_gate.reason` | **`NOT_ARMED`** |
| `halt` / `halt_local_env` | **`true` / `true`** |
| `SAFETY_OVERLAY:OPERATOR_HALT` | **162** |
| `order_progress.live_orders` | 0 |
| `account_exposure.account_total_worst_case_usd` | **`0`** |
| `day_realized.utc_day_realized_usd` | **`0`** |

Every `L5_LIVE_DECISION` row in the window carried
`key: "SAFETY_OVERLAY:OPERATOR_HALT"`, `order: null`, `intent: null`,
`armed: false`.

**And `authorization_preflight` reported `ok: true`, `fail: []`.**

## 2. Root cause

`Runner.try_arm` opens with

```python
if t is None or t.armed or self.halted(): return
```

`Runner.halted()` reads the local env halt `L5_LIVE_HALT`. So the operator
halt stops arming **before any numbered step of `authorize_arming` is
reached** — earlier than the shadow lock, the owner token, the deadline, the
campaign, everything.

`authorization_preflight` — the artifact an operator reads to decide a
window is ready — evaluated fourteen checks and **not that one**. It knew
about the independent watchdog latch (`halt_gate`), which was CLEAR, and
about the shadow lock, which the owner had cleared. It did not know about
the local halt, which was engaged.

So the readiness verdict said READY and the money path refused 162 times.

**This is an observability defect, not a safety failure.** The halt did
exactly its job. A readiness verdict that cannot see a blocker is worse than
no verdict, because it is believed.

## 3. My part in it

I reported the remaining blockers at 07:39Z as `shadow_lock` and
`campaign_approval`. That was wrong, and the data to see it was in front of
me: both heartbeats I captured — 05:29:19Z and 07:28:37Z — carried
`halt: true` and `halt_local_env: true`, and I printed the `halt` field in
my own diagnostic output at 07:28Z. I read the preflight's `fail` list and
treated it as the complete blocker set, which is precisely the mistake the
preflight's incompleteness invites. The window was lost to a prerequisite I
had already observed and did not act on.

## 4. The repair

**One expression, two callers.** `Runner.halted()` now delegates to a new
module-level `l5_pmus_live_runner.operator_halted()`, and
`authorization_preflight` calls that same function for a new
`local_operator_halt` row. A duplicated `os.getenv` in the preflight would
have been a second opinion, and a second opinion is what the incident was.

The row reports either way — `PASS` when clear, `FAIL` with
`engaged: true`, `env: "L5_LIVE_HALT"` and
`blocks: "Runner.try_arm returns before step 1"` when engaged — because a
check that appears only on failure leaves an operator unable to confirm it
was evaluated at all. An unreadable halt is `UNKNOWN`, and the preflight's
own label already states that `UNKNOWN and NOT_EVALUATED are never PASS`.

**It is kept distinct from `halt_gate`.** That row is the *independent
watchdog latch*, read over HTTP from a store the executor cannot write. This
one is the *local env halt* set on the service. Either alone stops arming,
so collapsing them would hide whichever was not chosen — and at 08:30Z the
latch was CLEAR while the local halt was engaged, which is exactly how the
window was lost.

**Nothing is weakened and nothing is cleared.** The preflight is read-only;
tests assert it cannot write the variable and that every non-empty value
still halts.

22 tests in `tests/test_l5_preflight_operator_halt.py`, including the
regression that `preflight.ok` cannot be `true` while the halt is engaged,
the 08:30Z shape exactly (shadow lock cleared, local halt engaged), and a
structural assertion that `Runner.halted` reads no environment of its own.

One existing test, `test_halted_is_the_local_env_var_and_nothing_else`, was
updated rather than deleted: it asserted on the *location* of the env-var
literal, which the delegation moved. It now asserts the property — `halted`
delegates and reads no env of its own, the delegate reads that one variable,
any non-empty value halts — plus the behaviour directly.

## 5. Provenance of the halt

* **Nothing in the codebase can set it.** No `os.environ[...]` write, no
  `putenv`, anywhere in `src/` or `scripts/`. The `emit("L5_LIVE_HALT", …)`
  call sites are *log lines* carrying the deadman cancellation report — they
  name the halt, they do not engage it. It can only have been set by an
  operator in Railway.
* **It predates this session.** `L5_LIVE_HALT` was already present in the
  first variable listing of the day (~05:25Z).
* **It has never been observed clear.** A log search for
  `"halt_local_env":false` across the entire retained window returns **zero
  rows**. Within retention it has been engaged continuously.
* **The exact moment and reason are not recoverable from here.** The Railway
  MCP surface returns variable *names* only — values are redacted and there
  is no variable-history tool. Establishing who set it and why requires the
  Railway dashboard's change history or the owner's own record. I am not
  going to guess at it.

## 6. Owner-controlled halt reset procedure

**Do not clear the halt to make a window work.** It is a deadman; the
correct order is to establish why it is set, then clear it deliberately.

Preconditions, each verifiable from the heartbeat before anything is
changed:

1. **Provenance established** — who set `L5_LIVE_HALT` and why, from the
   Railway change history. If that cannot be established, treat it as
   engaged for cause and do not clear it.
2. **Independent watchdog healthy and confirming** —
   `submission_gate.halt_latch` reads `CLEAR`, and
   `halt_latch_telemetry.sha256` is recorded. The watchdog service
   (`l5-halt-watchdog`) is SUCCESS. The latch is a *separate* control and
   must be checked on its own, not inferred from the local halt.
3. **No orders outstanding** — `order_progress.live_orders == 0`,
   `parked_orders == 0`, `pending_terminal == []`, `quarantined == []`.
4. **Reconciliation clean** — `unresolved_accounting.count == 0` with
   `blocks_live_authorization false`; `account_exposure.ok true` and
   `account_total_worst_case_usd == "0"`.
5. **Recorder durable** — `fill_calibration.readback.readable true`,
   `unreadable_lines 0`, `events_replayed_from_disk > 0`.
6. **A fresh single-window approval** — the expired 08:30Z document is
   **not** to be reused; its nonce cannot authorize another window
   (`WINDOW_NOT_IN_AUTHORIZED_SET`) and reusing it is not attempted.
7. **The new preflight row reads `local_operator_halt: PASS`** after the
   change, and `authorization_preflight.ok` is `true` for the exact
   approved slug — not for `market=null` at startup.

Only then clear `L5_LIVE_HALT` (set it empty; there is no "false" spelling),
and verify the row flips before the window opens rather than during it.

## 7. What is still open

* **The expired 08:30Z campaign is not to be re-run.** It is spent as an
  opportunity, not as a window: `windows_spent` is 0, but its `expires_utc`
  of 09:00:00Z has passed and the document is stale.
* **`L5_LIVE_HALT` remains engaged** and should until §5's provenance is
  established.
* **Zero venue-confirmed fills remain the program's blocker.** Nothing about
  this incident changed that, and nothing about it is evidence for or
  against the economics.
* **The launch orchestrator** (#214's second requirement — one approval,
  zero manual Railway steps) is a separate and much larger deliverable than
  this patch. This patch closes the specific gap that lost the window; it
  does not build that system.
