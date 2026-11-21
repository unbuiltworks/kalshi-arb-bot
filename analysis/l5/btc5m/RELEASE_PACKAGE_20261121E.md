# Deployment package for review — two-hour measurement campaign (NOT deployed, nothing armed)

## l5-btc5m-live
- Branch `release/l5-btc5m-campaign-20261121e`, commit `6b665d25a266f9c3e36fe696a420782a4a33799c`
- Currently deployed: `5424a3244efa77cc2fabcdf2a2f12119528493b4`
- Diff: `src/l5_behaviour_probe.py` (+171), `src/l5_btc5m_runner.py` (+77), one new test file (47 tests), deploy trigger.
  No change to the controller, the executor, the limits or the one-window document.
- Tests: 4,224 lane / probe / safety tests pass. Two lineage tests in `test_lane_a_probe_v0.py` fail with and without
  this change (container is missing packages); three Gemini files fail to import, as before.

### What changes
1. A second approval document, `L5_BTC5M_BEHAVIOUR_PROBE_CAMPAIGN`, that lists every window by exact slug.
   Code ceilings: 24 windows, all inside two hours, expiry = the last window's end, window loss <= $0.45,
   campaign loss <= $0.60, document no older than six hours, issued before its first window ends.
   It refuses unless `L5_BTC5M_CAMPAIGN_LOSS_HALT` is set and no looser than the document. Both documents set: neither.
2. While that document is set, the account-risk read refuses every submission if a market the lane settled today more
   than 40 minutes ago is missing from the account ledger's published results or differs by more than $0.005.
   (40 minutes = two passes of the account service's full feed read, ~10 min each, plus margin.)

## Settings the campaign also needs on l5-btc5m-live (order-COUNT budgets, not dollar limits)
The owner's per-campaign submission budgets read 90 each for OPEN / COMPLETE / REPLACE. The two live windows used
OPEN 2-4, COMPLETE 8-9, REPLACE 14-26 per window. At 90, requotes stop after about four windows.
Proposed for 24 windows: `L5_LIVE_AUTH_MAX_OPEN=150`, `L5_LIVE_AUTH_MAX_COMPLETE=400`, `L5_LIVE_AUTH_MAX_REPLACE=900`,
`L5_LIVE_AUTH_MAX_SUBMISSIONS=1450` (the flat total's current value has not been read). Without them the campaign
fails closed early; it does not trade past a budget.

## What will bind first
- Account drawdown limit ($1.00 from the equity baseline; $0.0478 used): the lane needs loss room >= its $0.60
  exposure ceiling, so it stops placing once the account is about $0.35 further down — BEFORE the $0.60 campaign halt.
- Exposure carry-over: a closed window's cost stays reserved until the venue settles it (~90 s), so the next window
  starts with less room and will trade less in its first minute or two.
- First multi-window live run of the persistent loop with real fills.

## Stops (all in code)
window loss $0.20; campaign loss $0.60 (new campaign id, counted from arming; windows 1-2 are not in it);
account daily/cumulative/drawdown limits; watchdog halt -> latch; unclean close or uncertain order -> latch;
ledger not reconciled after 40 min -> refusal; taker not allowed; envelope and approvals expire at the end.

## Acceptance after a disarmed deploy
boots `armed:false`, `can_submit:false`; effective config shows `behaviour_probe_campaign_document_set:false`;
watchdog reads the registry; zero open orders.

## Rollback
Reconnect to 5424a3244; restore the four budget variables.
