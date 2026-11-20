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
