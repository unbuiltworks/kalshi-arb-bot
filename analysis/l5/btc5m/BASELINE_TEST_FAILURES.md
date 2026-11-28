# Baseline test failures (not caused by the reliability release)

`tests/test_l5_btc5m_live_loop.py` — 11 of 16 tests fail on the deployed commit
`63475044f95566ec207bf60cbc98057a1dfe1808` and fail identically with the timeout and
hold-release changes applied.

Cause as observed: the file's fixture sets a fixed straddle price and expects an order at 0.08,
but does not supply the entry-EV rates, so every decision holds on
`ENTRY_EV_P_PAIR_NATURAL_NOT_SET` and nothing is placed. The file was last touched by
`650e7d2e1` ("give the entry-EV gate a reader"); whether it has failed since then was not
bisected.

| Class | Failing tests |
|---|---|
| `TestRepeatedFillsWork` | `test_a_filled_side_is_quoted_again`, `test_three_fills_on_one_side_all_replenish` |
| `TestAOneSidedFillIsManagedSafely` | `test_the_residual_is_named_from_confirmed_fills`, `test_a_completion_answers_it_without_buying_the_side_twice`, `test_no_loss_making_hedge_is_forced`, `test_an_unfilled_completion_leaves_the_residual_exactly_as_it_was` |
| `TestAnUncertainOrderFailsClosed` | `test_an_unreadable_order_halts_submission`, `test_it_stays_closed_while_the_read_keeps_failing`, `test_the_latch_lifts_only_on_a_clean_read` |
| `TestExpiryCancelsAndDisarms` | `test_the_working_order_is_withdrawn_by_exact_id`, `test_a_cancel_that_loses_to_a_fill_is_booked_not_assumed_away` |

These tests place nothing, so they currently verify nothing; the behaviours they name
(uncertain orders, cancel-versus-fill, repeated fills) are covered for this release by
`tests/test_l5_btc5m_hold_release.py`, `tests/test_l5_btc5m_continuous_participation.py` and
`tests/test_l5_btc5m_execution.py`. Repairing the fixture is separate work and is not in the
release candidate.

## Full L5 suite (all `tests/test_l5_*.py`)

Run on the release candidate `27cbdb32b`: 17,098 passed, 31 failed, 28 skipped. The same seven
files run on the deployed commit `63475044f` fail the identical 31 tests (lists diffed), so the
candidate adds none and fixes none.

| File | Failing |
|---|---|
| `tests/test_l5_btc5m_live_loop.py` | 11 (above) |
| `tests/test_l5_book_relative_inventory.py` | 8 |
| `tests/test_l5_hybrid_decision.py` | 6 |
| `tests/test_l5_balancing_budget.py` | 3 |
| `tests/test_l5_chain_validation.py` | 1 |
| `tests/test_l5_pmus_live_image_closure.py` | 1 |
| `tests/test_l5_shadow_controller_wiring.py` | 1 |

The causes of the 20 outside `test_l5_btc5m_live_loop.py` were not investigated.

## Triage of the quoting-path failures (28 tests in four files)

Method: `git bisect` between `650e7d2e1` (all four files pass) and the deployed commit, then a
rerun with the missing setting supplied.

| Origin | Tests | What changed | Why the tests fail |
|---|---|---|---|
| `eed10118c` (2026-11-19) "require the EV rates to be conditional-on-fill" | 11 `test_l5_btc5m_live_loop.py`, 6 `test_l5_hybrid_decision.py`, 4 `test_l5_book_relative_inventory.py` (`TestThroughTheRealExecutorPath`) | The entry-EV rates are unusable unless `L5_BTC5M_STRADDLE_P_BASIS=CONDITIONAL_ON_FILL` is attested. | The fixtures set rates without the attestation, so every decision holds on `ENTRY_EV_P_PAIR_NATURAL_NOT_SET` and no order is placed. |
| `9c9a2a47b` (2026-11-19) "charge the unmatched leg for adverse selection" | 4 `test_l5_book_relative_inventory.py` (`TestThePairIsPricedAsTheRealPair`), 3 `test_l5_balancing_budget.py` | A lone leg is charged 6 cents a share (`adverse_selection_per_share`). | The tests assert the earlier arithmetic (lone leg contributes exactly zero; EV = p_pair × spread). The gate now returns a negative EV and refuses. |

Check: with the attestation supplied by a test plugin and no code change, all 11 live-loop and
all 6 hybrid tests pass, including their uncertain-order, cancel-versus-fill, partial-fill,
restart and repeated-fill assertions. The remaining 11 still fail on `EV_NOT_POSITIVE` /
`UNMATCHED_CEILING`, i.e. on the adverse-selection charge.

Classification: outdated test expectations. In every one of the 28 the failing assertion is
"an order was expected and the entry gate placed none". None shows an order sent that should
not have been, a wrong price or size, a miscounted fill, or a hold or cap error. Both origin
commits tightened the entry gate and are already in the deployed image; the release candidate
touches neither.

Limit of this triage: four executor-path tests in `test_l5_book_relative_inventory.py` never
reach their fill and partial-fill assertions. The same behaviours are exercised under the
behaviour-probe waiver by `test_l5_btc5m_continuous_participation.py` and
`test_l5_btc5m_hold_release.py`. The tests were not repaired.
