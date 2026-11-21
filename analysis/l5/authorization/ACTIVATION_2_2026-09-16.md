# CP1 three-market probe — second activation, after the concurrency fix

**Authorization:** owner, 2026-09-16 — "…and only then restore the
already-authorized three-market CP1 probe."

## Why the first activation stopped

ARMED 21:13:02Z → SUBMITTED 6 → FILLED 0 → nothing settled, nothing realized,
$0.00 moved, account flat. Six maker orders on
`aec-dota2-yes-tm-2026-09-16` at qty 0.22 (exactly what $0.10 target / $0.125
per-leg yields at a ~$0.45 touch), all `no_fill_cancelled`. The campaign then
could not open market 2: `consumption()` counted a market open from
**admission**, and a market that never filled never settles, so it held
`max_concurrent_markets=1` permanently.

That is fixed. A market is concurrently open while it has a live or unresolved
order, or unsettled fills. See `CONCURRENCY_ACCOUNTING_2026-09-16.md`.

## Pre-activation verification, on the serving image

Serving commit `5a8df1229edd`, deployment `73e1b4bf`, SUCCESS 22:05:58Z.

| Check | Observed |
|---|---|
| Halt state | `SHUTDOWN_VERIFIED` |
| Our open orders | `sweep.seen 0`, `still_open_ours []` |
| Foreign open orders | `[]` |
| Held inventory | `held_preserved []` |
| Active rung | `CP1`, per-market cap `$5.00` |
| Effective caps | market `$5.00`, residual `3` units, daily stop `-$6.00` |
| After restore | pinned `CP1`, stored `CP1`, effective `CP1`, `tightened_by_stored []`, `no_dimension_loosened true` |
| Sizing | `target_notional_usd 0.10`, `max_leg_notional_usd 0.125` |
| Campaign | `markets_per_campaign 3`, `concurrency 1`, `max_actions_per_session 6` |
| Mandate | loaded, refusal `null` |
| Realized today | `-$0.0916`, budget `-$6.00`, `budget_permits_normal_operation true` |

## Mandate: reused, not replaced

Re-validated **at activation time** against the real validator rather than
assumed:

* `load_mandate()` → loaded, refusal `null`
* `service` == `l5-r1-probe` exactly
* `expired()` → `False`; expiry `2026-09-17T20:36:35Z`, **22h29m remaining**
* a dry `admit()` on a representative order → `admits: True`

`OWNER-2026-09-16-CP1-CONTINUOUS-PROBE`: $5.00/market, $15.00 total, $3.00
residual, $6.00 daily loss stop, 1 concurrent market, 3 total markets. Reused
unchanged. Nothing was extended or silently rewritten.

## What the probe has left to spend

**Two markets, not three.** The volume `l5-r1-probe-state` is mounted at
`/state/l5-r1-probe`, so the durable ledger survived the restart: the six
commits from 21:13Z are still on disk and
`aec-dota2-yes-tm-2026-09-16` still counts as one of `max_total_markets=3`.

That is the fix behaving as specified, not a side effect. A zero-fill attempt
releases the **concurrency** slot and keeps its claim on the **total**. The
alternative — releasing it from both — is the unlimited-market-scanning failure
the authorization explicitly forbade.

Spend so far is `$0.00`, so the full `$15.00` campaign envelope and the full
`$5.00` per-market envelope remain.

## Standing instructions for this run

* Do not force fills.
* Do not modify the controller because the first orders fail to fill.
* Do not change sizing, model, ACT/WAIT threshold, k, rung, mandate, or risk
  limits during the probe.
* Report `ARMED → SUBMITTED → FILLED → SETTLED → REALIZED`, and call nothing
  realized before the venue confirms it.
* If activation exposes any new material discrepancy: HALT and report, rather
  than modifying another safety mechanism.

Removing `HALT` does not authorize trading. It un-halts the process; orders
additionally require the valid mandate above, which
`test_the_halt_and_the_authorization_are_consistent` enforces as an invariant
in both directions.
