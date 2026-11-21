# 1545z: a whole market window lost to a single arm attempt

**Status:** P0 production defect, found live, fixed, regression-pinned.
**Found:** 2026-09-27 15:45:23Z, during the armed B' (RESIDUAL_CAP_k2 /
PROSPECTIVE_NET) probe, session `k2pn-20260927T145529Z-337c3275458d`,
deployment `a5738478`, commit `7a6d145`.

## What was observed

The 1545z window ran start to finish with:

- `armed: false`
- `preflight: "PASSED"`
- `owner_arm_token: true`
- `halt: false`
- `ORDER:NOT_ARMED` on 73 decisions the engine wanted to act on

Every precondition for arming was satisfied and nothing was armed.

## Authoritative sequence

| UTC | Event |
|---|---|
| 15:45:20 | `L5_LIVE_PREFLIGHT {"preflight":"PASSED"}` for 1545z |
| 15:45:23 | `L5_LIVE_RECONCILE` reports drift on 1530z; one terminal order (`l5f-dca9e85445aa18f79cfa4d7a`) not yet booked; `retire_refused:UNBOOKED_TERMINAL_ORDERS: 1` |
| 15:45:23 | `L5_LIVE_DISARMED {"why":"orders in an UNCERTAIN state; refusing to arm"}` |
| 15:45:31 | that order resolves terminal, `released`, `resolved` |
| 15:45:32 | `L5_LIVE_SETTLED` retires 1530z |
| 15:45:35 | `L5_LIVE_RECONCILE {"reconciled":true,"drift":{}}` — uncertainty gone |
| 15:45:55 → 15:59 | every decision refused `ORDER:NOT_ARMED` |

The 15:50:33 heartbeat is clean on every axis — `drift {}`, `parked_orders 0`,
`pending_terminal []`, `quarantined []`, `live_orders 0`, `halt false`,
`owner_arm_token true`, `preflight PASSED` — and still `armed: false`.

**The refusal at 15:45:23 was correct.** An order whose terminal state is not
yet booked may be live and consuming the envelope; refusing to arm on it is the
fail-closed behaviour we want. The defect is that the refusal was never
revisited after the condition cleared twelve seconds later.

## Cause

`try_arm()` was reachable from exactly one place — inside `maybe_preflight()`,
after a PASS:

```python
def maybe_preflight(self, now: float) -> None:
    t = self.transport
    if t is None or self.preflight_status == "PASSED":
        return                      # <-- arming unreachable from here on
    ...
    if self.preflight_status == "PASSED":
        ...
        self.try_arm()              # <-- the only call site
```

`preflight_status` is set to `PASSED` by that same block, so the guard on the
first line closes the only door to `try_arm` for the rest of the market window.
**Arming got exactly one attempt per market.** `roll()` resets
`preflight_status` to `NOT_RUN` for each new market, which is why 1445z, 1500z,
1515z and 1530z all armed normally and why the failure is per-window rather
than permanent — but one window is one quarter of the four informative markets
this probe was authorised to collect.

The two conditions `try_arm` refuses on are both explicitly transient:

- `not self.reconciled` — cleared by the next authoritative reconciliation;
- `store.orders_uncertain()` — documented in `InventoryStore` as "Cleared ONLY
  by an authoritative venue reconciliation", i.e. designed to clear later.

This is the same shape as the reconcile latch fixed on 2026-09-26 (see
`reconcile_if_needed`, "ONCE PER PROCESS WAS A LATCH"): a fail-closed state
with no path back out. That fix gave reconciliation a retry; arming never got
one.

## Fix

The smallest additive change — retry arming on every tick once preflight has
passed, without ever re-running the preflight itself:

```python
def maybe_preflight(self, now: float) -> None:
    t = self.transport
    if t is None:
        return
    if self.preflight_status == "PASSED":
        self.try_arm()
        return
    ...
```

Retrying is free and touches no venue:

- `try_arm` returns on its first line when already armed or halted;
- `reconcile_if_needed` returns immediately on a confirmed, certain book;
- `PmusTransport.arm()` is a local gate check (preflight report + the dual-key
  env token) and issues no request.

Nothing else changes. Preflight is still run once per market. The refusal is
still logged only on its first occurrence in a window, so a long refusal does
not flood the log; its duration shows in `arm_refused_orders_uncertain` /
`arm_refused_unreconciled`.

## What this does NOT change

k stays 2. The policy stays `RESIDUAL_CAP_k2` / `PROSPECTIVE_NET`. The frozen
`L5_NATIVE` rule stays `030d8bf`. The envelope is untouched:
`max_usd_per_market 5`, `max_abs_residual 10`, `max_total_exposure_usd 5`,
`max_concurrent_markets 1`, `max_live_orders 8`,
`daily_realized_loss_halt_usd 5`. Maker behaviour, pair-cost behaviour and the
loss halt are untouched. This is a liveness repair on the arming path only.

It cannot loosen a gate: arming still requires a PASSED preflight in this
process, the owner dual-key token, a reconciled book, no order uncertainty and
no halt. It only allows those same five conditions to be asked more than once.

## Regression

`tests/test_l5_arm_retry.py`, 8 tests. Five of them fail against `7a6d145` and
pass after the fix:

- `test_a_passed_preflight_still_retries_the_arm`
- `test_an_uncertainty_that_clears_does_not_cost_the_window` — the literal
  1545z sequence: refuse on uncertainty, clear it, arm on the next tick
- `test_a_refusal_is_logged_once_however_long_it_lasts` — 40 refusals, one
  `L5_LIVE_DISARMED`
- `test_an_unreconciled_book_is_retried_on_the_same_terms`
- `test_the_retry_costs_nothing_once_armed` — 50 further ticks, one `arm()`,
  zero extra reconciliations

and three that hold on both sides, pinning what must not change:

- `test_the_retry_never_re_runs_a_passed_preflight`
- `test_a_halted_runner_is_never_armed_by_the_retry`
- `test_a_failed_preflight_still_takes_the_preflight_path`

## Cost of the defect

1545z produced no orders and no fills, so it is **not** an informative market
and is not counted. No money was lost and no risk was taken — the failure mode
is fail-closed. What it cost was one of the four authorised windows, and the
next-tick reporting of it.

## Probe state at the time of the fix

Informative markets: **2 of 4** (1515z, 1530z). Realized total
`-2.51640000`; `legacy_realized_today -3.54110000` against the $5 cumulative
loss halt, i.e. about $1.46 of headroom.
