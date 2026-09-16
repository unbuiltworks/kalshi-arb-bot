# CP1 activation — HALT removed

**OWNER AUTHORIZATION 2026-09-16, "final CP1 activation".** The HALT latch is
removed and the bounded three-market continuous live probe is armed.

## The authorized envelope, verified at runtime before removal

Deployment `3dc110e7`, commit `78ac58e6b76d`, halted disclosure at 20:51:54Z:

| | authorized | runtime |
|---|---|---|
| per-market spend | $5.00 | `effective_per_market_cap_usd "5.00"` = min(rung $5.00, mandate $5.00) |
| total campaign spend | $15.00 | `max_total_spend "15.00"` |
| residual | $3.00 / 3 contracts | `effective_residual_cap_units 3` |
| concurrent markets | 1 | `max_markets 1`, rung `concurrency 1` |
| total markets | 3 | `max_total_markets 3`, `markets_per_campaign 3` |
| daily realized-loss stop | −$6.00 | `effective_daily_loss_stop_usd "-6.00"` |
| mandate expiry | 24 h | `2026-09-17T20:36:35Z` |
| k | 0.00619 unchanged | never touched |
| CP1 sizing | 0.10 / 0.125 | `target_notional_usd "0.10"`, `max_leg_notional_usd "0.125"` |

`after_restore` showed `pinned_rung CP1`, `stored_rung R3L`,
`effective_rung CP1`, `tightened_by_stored []`, **`no_dimension_loosened true`**
— B3 resolving the volume's stale R3L without loosening anything.

Account immediately before removal (20:56:58Z): `SHUTDOWN_VERIFIED`,
`sweep.seen 0`, `still_open_ours []`, `foreign_open []`, `held_preserved []`,
`refusals 0`, realized today −$0.0916 against a −$6.00 budget. **Flat, zero
open orders.**

## The security test was restated, not removed

`test_the_repo_is_currently_halted` asserted `HALT.exists()` unconditionally,
and it fired the moment HALT was removed — which is what it was for. The
removal stopped there until the owner authorized this specific change.

It is now `test_the_halt_and_the_authorization_are_consistent`, a consistency
invariant that is **stronger** than what it replaced:

- **HALT present → must be halted.** Always a safe state.
- **HALT absent → requires a committed, parseable, unexpired mandate naming
  exactly `l5-r1-probe`.**

The original only asserted a file exists; it would have passed again for
anyone who re-added HALT regardless of whether any authorization existed, and
said nothing about the mandate. This one ties the two together in the
dangerous direction.

**Absence of HALT is never authorization on its own** — stated as its own test.

The rule lives in `activation_authorized()` so the live tree and the
adversarial cases are checked by the same code. All seven cases the owner
enumerated pass:

| HALT | mandate | result |
|---|---|---|
| present | none | halted ✓ |
| present | valid | halted ✓ |
| absent | none | **fail** — `no_halt_and_no_mandate` |
| absent | malformed | **fail** — `mandate_unusable` |
| absent | expired | **fail** — `mandate_expired` |
| absent | wrong service | **fail** — `mandate_wrong_service` |
| absent | valid exact CP1 | permitted ✓ |

Nothing was deleted, skipped, xfailed, or disabled. Full regression suite:
**4959 passed, 27 skipped, 1 xfailed, 0 failed.**

## What still stops this probe

Removing HALT un-halts the process; it does not widen anything.

1. **The wire guard.** Every `POST /v1/orders` is admitted against the
   immutable mandate by economic effect, not by stage name.
2. **`min(rung, mandate)`** — a stale rung can never raise the envelope.
3. **B3** — stored state can only tighten the pin, never replace it.
4. **The mandate expires** in 24 h and is re-read every campaign pass, so an
   unattended process stops itself when its authorization runs out.
5. **The HALT latch can be re-committed at any time** and is read before
   `armed()` on the next boot.

## Accounting labels for this probe

**ARMED → SUBMITTED → FILLED → SETTLED → REALIZED**, reported separately. A
fill is not realized profit. A settlement without the venue's confirmed
cashflow stays `SETTLED_REALIZED_UNCONFIRMED` and out of every net.
