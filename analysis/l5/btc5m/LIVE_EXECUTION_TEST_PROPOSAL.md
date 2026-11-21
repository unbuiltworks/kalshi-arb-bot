# BTC5m bounded live execution test: proposal for owner approval

Prepared 2026-11-21. NOTHING HERE IS AUTHORIZED. No deployment, arming or
order follows from this file. Each numbered step needs its own explicit
owner approval.

## Purpose

Venue-confirmed fills and settlements from the existing controller, to
replace the replay's inferred fills as the measurement of: what a filled
bid is worth (fill edge), what pairs actually cost, and what residual is
left. It is not expected to make money: both earlier live windows lost
about 1.5 cents and every replay is negative. The loss is the price of
the evidence and is bounded below.

## Readiness of each component

| Component | Evidence | State |
|---|---|---|
| Simultaneous UP/DOWN quoting | 17:05Z live window: both sides quoted, 39 orders, 21 fills | venue-confirmed |
| Continuous maker replenishment | same window: sides re-quoted after fills up to the side cap | venue-confirmed |
| Adaptive repricing, exact-ID cancel | same window: requotes cancelled by id, clean close, 0 orders left; hold-release fix deployed since | venue-confirmed |
| Controlled residual exposure | side cap $0.30, unmatched cap $0.10, window-loss ceiling $0.20 all enforced in that window; no envelope refusals | venue-confirmed |
| Order acknowledgement, fills, uncertain-order latch | live-loop tests REPAIRED in this change: 16 of 16 now run and pass (they had placed nothing since 2026-11-19 because the fixture lacked the rate attestation) | tests |
| Settlement reconciliation | both live windows reconcile to the venue's position rows to the cent | venue-confirmed |
| Loss accounting across services | accounting releases prepared, NOT deployed (steps 1-2 below) | pending approval |
| Watchdog | independent service, sweeping every 20 s, 0 open orders | running |
| Inventory-aware allocation (heavy-underdog stop) | 31 tests; replay only | NEVER RUN LIVE: this test is its first venue evidence |
| Book-read timeout guard | deployed after the 14:20Z crash; unit tests | not yet exercised live |

## Code to deploy

| Service | Now running | Proposed |
|---|---|---|
| `l5-pmus-live` | `cfac6707c` | `1e75294f3` (`release/l5-pmus-live-loss-publication-20261120`): publishes today's retired results and the lifetime basis; read-only additions |
| `l5-btc5m-live` | `27cbdb32b` | the commit that adds this file on `claude/runner-executor-verification-utzgrh` (its SHA is in the report that accompanies it), to be frozen as `release/l5-btc5m-inventory-20261121` on approval |

The proposed BTC5m commit = the prepared accounting release `a87493baf`
plus three source files changed (`l5_btc5m_controller.py`,
`l5_btc5m_execution.py`, `l5_btc5m_runner.py`): five decision switches,
ALL OFF unless their exact token is set. With none set the controller's
decisions are those of `27cbdb32b`. Image file list unchanged from
`a87493baf`; image-closure tests pass.

## Steps (each needs explicit approval)

1. Deploy `l5-pmus-live` -> `1e75294f3`. No variable set. Check: healthy,
   0 orders, `lifetime_basis` present and reconciled. If not, stop.
2. Deploy `l5-btc5m-live` -> proposed commit, DISARMED. No variable set.
   Check: `can_submit false`, effective-config line lists the new
   switches as off, account-risk line shows the lifetime basis read.
3. Arm ONE window at a time, four windows in all, each a separate arm
   and a separate disarm, never automatically repeated:

   | Window | Controller | Only variable that differs |
   |---|---|---|
   | A | as deployed live on 2026-11-20 | none |
   | B | + heavy-underdog stop | `L5_BTC5M_STRADDLE_HEAVY_UNDERDOG=STOP_ADDING` |
   | C | as A | none |
   | D | as B | as B |

   Windows are chosen at arming time (next window at least 12 minutes
   ahead), not picked for their conditions. After each window: read the
   venue's position row, reconcile, and only then arm the next.

## Limits (identical to the window authorized on 2026-11-20; none raised)

| Limit | Value |
|---|---|
| Markets | one BTC5m window at a time |
| Order type | post-only maker bids; taker not allowed (`taker_allowed false`) |
| Clip | $0.05 |
| Side cap / unmatched cap | $0.30 / $0.10 |
| Window-loss ceiling | $0.20 |
| Envelope per window (aggregate, per market, daily) | $0.60 |
| Campaign | `max_windows 1` per arm |
| Worst case over four windows by the ceilings | $0.80 |
| Account drawdown allowance remaining | $0.97 of $1.00 (unchanged) |
| Daily halt | unchanged |

## Stop conditions (any one ends the test; no further window is armed)

- a window's settled loss exceeds $0.20, or cumulative settled loss over
  the test reaches $0.60;
- the service crashes or restarts while armed;
- any order's state is uncertain (the latch asserts), or a cancel is not
  confirmed terminal;
- the watchdog cancels anything, or reports a read error;
- our ledger and the venue's position row disagree by more than $0.005;
- an envelope or reservation refusal that is not explained by a stated
  limit;
- any order that is not post-only, or any fee charged.

## Rollback

- Disarm (the state between windows and after any stop): arm token
  empty, `L5_SHADOW_ONLY=1`, probe and campaign documents empty,
  envelope expired, heavy-underdog variable empty.
- Code: point `l5-btc5m-live` back at
  `release/l5-btc5m-reliability-20261120` (`27cbdb32b`) and `l5-pmus-live`
  at `release/l5-pmus-live-equity-and-correction-20261120` (`cfac6707c`).
- A cancelled order does not flatten filled inventory: any filled shares
  are held to settlement (at most the caps above).

## What will be reported after each window

Orders, fills with venue timestamps, fees, the venue's position row,
matched and unmatched lines, the price 5/15/30 s after each fill (the
venue-confirmed fill edge), and the same window replayed with the
inferred-fill rule, so the replay is calibrated against real fills.
Lifecycle labels: submitted, filled, settled, realized. Four windows will
not establish profitability or the A/B difference; they establish
whether the replay's fill model is wrong, and by how much.

## Safety regressions run for this proposal (2026-11-21)

`tests/test_l5_*.py`: 17,428 passed, 14 failed, 28 skipped. The 14 are
the remainder of the 31 documented in `BASELINE_TEST_FAILURES.md` after
this change repaired 17 (11 live-loop, 6 hybrid-decision) by supplying
the rate attestation their fixtures lacked. The 14: 8
`test_l5_book_relative_inventory`, 3 `test_l5_balancing_budget` (both
assert the entry-gate arithmetic from before the adverse-selection
charge), 1 each `test_l5_chain_validation`,
`test_l5_pmus_live_image_closure`, `test_l5_shadow_controller_wiring`
(other services; not investigated). BTC5m image-closure tests: 218 passed.
