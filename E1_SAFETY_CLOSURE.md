# E1 safety workstream — CLOSED, 2026-10-10

Closed at the owner's instruction. Branches preserved, nothing deployed,
nothing armed, no Railway setting changed. The service remains pinned to
`b88ec5eec` with `startCommand python3 -m scripts.l5_e1_prelive_validation`
and `restartPolicyType NEVER`.

## Branches preserved

| branch | head | contents |
|---|---|---|
| `claude/e1-shutdown-recovery` | `77a3f81a3` | handover reachable on every exit; signal dispositions saved and restored |
| `claude/e1-obligation-ledger` | `99863000e` | durable obligation ledger; per-order fill attribution; O_EXCL reservation |
| `claude/e1-burst-forensics` | `1c88c6354` | the 1215z burst reproduced from its root cause |
| `claude/e1-integration-review` | `864ff2d3b` | both patches on `b88ec5eec`, final versions |
| `claude/pmus-q1-executor-pricing-boundary` | `0339f93d0` | unrelated executor pricing fix, isolated |

## What was found and fixed

**Two independent production defects.**

1. **The handover was reachable from one place only** — the `past_cutoff`
   branch inside `run_cohort`'s loop. A normal exit before the cutoff,
   SIGTERM (how Railway stops a container), SIGINT and any escaping
   exception all returned with owned orders resting. This is the recorded
   2026-10-08 incident: twenty 0.13-share orders left on the 1230z window
   because "E1 was disarmed and its process stopped, which means ITS OWN
   DEADMAN NEVER RAN".

2. **The one-outstanding-first-leg rule was read-your-writes dependent.** A
   venue that acknowledges a POST and then denies the order exists --
   absent from `/v1/orders/open` AND 404 on `/v1/order/{id}`, both
   endpoints agreeing so nothing fails closed -- let a second first leg
   through, and more as the denial persisted, bounded above only by the
   attempt latch at twenty. Our own durable record is now primary: the
   venue may confirm, advance and reconcile an obligation; it may never
   erase one.

The cancellation mechanism itself was already correct and was not changed:
exact owned order id, ownership by our own registry, an unattributable
order left alone, every id re-verified against the venue.

**Five defects in my own patches, each found by a test rather than by
reading:** an inert reservation that two processes could both win; an empty
file between `O_EXCL` create and fsync, so a loser could not name the
holder; a hard link that let a stale temp file truncate the live
reservation through it; a circular fill-accounting check that made
`TERMINAL_FILL_UNACCOUNTED` unreachable; and signal handlers that leaked
process-wide because `install` never recorded what it replaced.

## Final regression comparison

Scope `analysis/l5/e1_cohort` — 100% of the changed production code. Each
run solo, single process, integrity-verified.

| run | commit | passed | failed | skipped | xfailed |
|---|---|---|---|---|---|
| pristine | `b88ec5eec` | 717 | 0 | 0 | 0 |
| shutdown | `77a3f81a3` | 736 | 0 | 0 | 0 |
| integrated | `864ff2d3b` | 812 | 3 | 0 | 0 |

## The three remaining failures — open, characterised, NOT fixed

`test_e1_live_path.py::test_armed_main_reaches_run_cohort`,
`::test_two_distinct_windows_get_two_distinct_authorized_slugs`,
`::test_a_restart_resumes_rather_than_resets_the_cohort`.

All three refuse with `E3_OUR_ORDER_IS_OUTSTANDING:1`. They assert E1 opens
a first leg in each of two windows **while the first window's order is
still resting at the venue** -- a violation of the frozen
`max_outstanding_orders: 1` invariant. They passed on the pristine baseline
only because the fake venue denied the order existed, so absence resolved
it.

**The production behaviour is correct; the test expectations encode the
unsafe assumption.** Correcting them is a reviewed change, not something to
do unilaterally: I attempted it three times and was wrong each time (first
deselected the neighbouring test, then wrote a mirror that ran at reader
construction before any order existed and was therefore inert, then made
the fake truthful and thereby confirmed the refusal is real).

## Full-suite limitation

A full-suite comparison is **not obtainable in this container**, and the
blockers are pre-existing on `b88ec5eec`:

* a cross-test wedge in `tests/test_l5_rung_r2c.py` -- thread dump:
  `shutdown_settle_pass → save_settle_cursor`, which writes the durable
  round-robin cursor to `/state/pmus-q1`, absent here, and swallows the
  failure, so the cursor never advances and the drain re-audits the same
  head forever;
* a test near 90% whose captured output exhausts memory -- `MemoryError` in
  `_pytest/capture.py` at `self.tmpfile.read()`.

Neither is in the E1 execution path. **~11,100 tests outside
`analysis/l5/e1_cohort` are unverified on these branches.** The patches
touch no file outside that package, so the blast radius is bounded by
construction -- an argument, not a measurement.

Worth recording separately: `pmus_l5_continuous_session` defaults its state
to `/state/pmus-q1` while the deployed volume is `/state/pmus-ws`. If that
path is ever unwritable in production the settle drain does not terminate,
and the swallowing hides it.

## Verdict A — strictly disarmed deployment: **NO-GO**

Not because the code is unsafe. Because:

* **SIGKILL/OOM is not covered and cannot be** by an in-process handler;
  `coverage()` reports `in_process_handler_covers_sigkill: False`. The
  durable latch, the obligation ledger and startup reconciliation cover a
  hard kill by REFUSING, not by recovering. **No out-of-process recovery
  actor exists**: `restartPolicyType: NEVER` means nothing re-runs, so
  orders left by a hard kill still need the operator tool
  `scripts/l5_e1_cancel_by_id.py`.
* **A deployment would be inert.** The Railway startCommand runs
  `l5_e1_prelive_validation`, which never reaches `run_cohort`. Shipping
  these patches would deploy code the running entrypoint does not execute.
* Three visible failures and ~11,100 unverified tests.

Conditionally GO once the three test expectations are corrected under
review and an out-of-process recovery path is authorized.

## Verdict B — arm with real capital: **NO-GO**

Independent of any test result. The execution safety work is sound where it
is testable -- outstanding-order exposure is ledger-primary and charged at
the unobserved ceiling, the reservation survives a crash at every
filesystem transition, startup reconciliation refuses with zero POSTs -- but
safety infrastructure is not expectancy.

The only E1 window that ever filled produced **2.60 shares of unhedged
directional inventory, 20x intended size, with no complement**, and paid
**+$1.3260 because the resolution went our way**. The identical sequence on
a "No" loses the full $1.2740. No matched pair, no spread capture, no
rebate. That is not evidence of a working strategy.
