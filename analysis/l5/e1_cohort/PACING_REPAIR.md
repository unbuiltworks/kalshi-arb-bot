# E1 REFUSAL-PACING REPAIR

| | |
|---|---|
| superseded fingerprint | `b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1` |
| **new fingerprint** | **`b0f15c958c8fee779e3b9518885c7ddd3ea379afd5bb9759c44f878d7322f3ba`** |
| invariants | 163 -> **177** |
| session preserved | `E1-COHORT-20261008T005816Z-051888d8`, **1/12 used, 11 remaining** |

## The defect, as measured

Deployment `74f404b1` armed, opened `cpc-btc-updown-15m-2026-10-08-0100z`, and:

```
iterations        960   == the old cap, MAX_PLACEMENTS * 4
every refusal     E9_PRICE_ABOVE_CEILING:0.62
placements        0
windows_used      1 of 12
network           01:01:23.38Z -> 01:01:24.53Z, then nothing
```

The $0.60 ceiling was right and the book was 0.62, so nothing should have been
priced. The fault was that `run_cohort`'s refusal branch was a bare `continue`
with no wait, so a window that needed fifteen minutes of patience got 960
refusals in about one second. **The cap, not the market, ended the cohort.**

XR2W Attempt 6 -- our only evidenced fill -- paced itself: 1,230 polls over
6,150.1 s.

## What changed

**`pacing.py`** (new). Classifies a refusal and computes a bounded,
deadline-aware wait.

- `PRODUCTION_PACE_S = 1.0`, `MIN_PACE_S = 0.05`. No paced wait is ever zero,
  so "no busy loop" is a property of the code.
- **Fail closed by construction:** only an explicit allowlist is paced.
  Anything else -- including a refusal this code has never seen -- is terminal
  and stops the cohort.
  - *paced* (statements about a book that moves): `E3_*` outstanding
    order/residual, `E4_WINDOW_NOT_OPEN`, `E4_PAST_HANDOVER_TTE`, `E7_*`,
    `E8_*`, `E9_*`, `E10_*`, `E11_WINDOW_LATCH_EXHAUSTED`,
    `PROJECTED_WINDOW_EXPOSURE_*`, `WINDOW_<n>_ATTEMPT_<k>_REFUSED_MAX_20`.
  - *terminal* (safety): `E1_DISARMED`, fingerprint not pinned, `E12_*`,
    `QTY_*`, `E11_LATCH_UNAVAILABLE`, `COHORT_PLACEMENT_CAP_240`,
    `FORWARD_LOOKING_LOSS_BOUND`, `VERDICT_IS_*`, `E4_WINDOW_OUT_OF_RANGE`.
- **Two deadlines, and they are not the same instant.** Before the trading
  cutoff the wait is clamped to it, so the last tradeable instant is used
  rather than slept through. After the cutoff it waits out the window so the
  provider rolls over.

**`pmus_provider.py`.** The snapshot now carries `trading_cutoff_unix`
(`window_end - HANDOVER_TTE_S`, the same two values that flip
`within_handover_tte`, so the pacing horizon and the eligibility rule cannot
disagree) and `window_end_unix`.

**`runner.py`.** `run_cohort` takes `sleep_fn`/`pace_s`. Both refusal paths --
the preflight refusal and the `Ineligible` raised by the re-check inside
`submit_first_leg` -- classify and either wait or stop. `Ineligible` now
carries its refusal list, so the classification sees the refusals that
actually fired rather than a re-derived set. A transient empty snapshot is
paced up to 30 times instead of being read as the end of the cohort; a spent
window budget still reads as genuine exhaustion.

**The iteration cap is a net, not a budget.** `960` -> `11760`
(`windows * window_len_s / PRODUCTION_PACE_S + max_placements * 4`), derived
from the *production* pace. Reaching it is an anomaly, not a normal ending.
`WINDOW_LEN_S` is taken from `pmus_identity.WINDOW_S` -- the same 900 that
computes `window_end` -- so the horizon and the boundary cannot drift.

## What did NOT change

Every bound, asserted directly in `test_the_ceiling_itself_is_untouched_by_pacing`:
`0.13` shares, `<= $0.60` first leg, `<= $0.96` fee-inclusive pair, 20
placements/window, 12 windows, `$1.56`/window, `$5.00` cohort-wide
forward-looking ceiling. Maker/post-only, no taker rescue, no chasing, no
requote. An ambiguous submission is still never retried, and no stop condition
can be bypassed -- a verdict stop is still evaluated at the top of every
iteration.

## Proof

**46 new tests** in `test_e1_pacing.py`; whole package green.

- `test_062_is_refused_then_059_becomes_eligible` -- the decisive one. 0.62 is
  refused under the unchanged $0.60 ceiling, the clock advances through paced
  waits, the book improves to 0.59, and the same loop places at `0.59`.
- `test_persistent_refusal_paces_to_the_cutoff_and_places_nothing` -- a book
  that never improves is observed for **> 600 s** of simulated time with zero
  placements, zero latch slots and no cap termination.
- `test_the_old_960_cap_could_not_hold_one_paced_window` -- the arithmetic that
  made 2026-10-08 inevitable: 900 observations in one window, 960 for twelve.
- `test_pacing_consumes_no_attempt_no_latch_and_no_risk_budget` -- the owner's
  item 6, measured on the tracker: after 100+ paced observations,
  `placements 0`, every `window_attempts` counter 0, every window exposure 0,
  `venue_errors 0`, forward-looking total `0`, no `.submitted` file.
- `test_an_unrecognised_refusal_is_terminal_so_it_fails_closed`.
- `test_an_ambiguous_submission_is_never_retried`.
- `test_pacing_places_nothing_after_the_trading_cutoff`,
  `test_pacing_waits_out_our_own_resting_order_and_never_doubles_up`.
- Restart behaviour is covered by the pre-existing
  `test_a_restart_resumes_rather_than_resets_the_cohort` and
  `test_twelve_restarts_do_not_grant_twelve_cohorts`, which now exercise a
  **complete paced cohort** and still pass.

### Two things found on the way, stated plainly

1. **A guard that had never run.** `conftest.py` contained
   `test_production_defaults_are_what_we_think_they_are`, asserting the shipped
   readiness bound. pytest does not collect tests from `conftest.py`, so it has
   never executed. It is now a real collected test
   (`test_the_production_readiness_bound_is_what_ships`) and reads the SOURCE
   literals, because the suite patches the module values.

2. **A discrepancy I did NOT change.** `pmus_provider.HANDOVER_TTE_S` is
   `60.0`, while the inherited `SPEC["timing_explicit"]` says
   `tte_shutdown_s: 120.0` and `placement_allowed_while_tte_gt_s: 120.0`. The
   provider is therefore LOOSER than that spec section by 60 s. That section is
   XR2W-inherited (it sits beside `PMUS_EXEC1_*` env names and
   `max_orders_this_experiment: 1`, neither of which describes E1), so it may
   simply not apply -- but I will not quietly move a risk boundary. Pacing uses
   the gate that is actually enforced, `HANDOVER_TTE_S`, so the horizon and the
   rule agree. **Flagged for your decision.**

## Scope note on "cancellation/deadman"

E1's runner has no deadman of its own -- there is no `handover` or `withdraw`
function in this package, and cancellation lives in XR2W's order modules, with
fill-races-a-cancel accounting already tested in `test_e1_cohort.py`. I tested
what pacing can actually affect: no placement while an order of ours is
outstanding, and no placement after the trading cutoff. I did not write a
deadman test for machinery that is not here.

## Accounting

**No order, fill, settlement or realized P&L is claimed anywhere in this
repair.** Nothing was placed; realized P&L remains **$0.00**. Deployed
**DISARMED**. The session's one consumed window and both invalidated cohorts
are untouched.
