# Deployment package for review — 2026-11-21 (NOT deployed; both services disarmed)

## 1. l5-btc5m-live
- Branch `release/l5-btc5m-inventory-20261121d`, commit `5424a3244efa77cc2fabcdf2a2f12119528493b4`
- Currently deployed: `ac3c7f9e758e2bd9a518507c82dcd7caa475821c`
- Diff: `src/l5_btc5m_execution.py` (+62), `src/l5_btc5m_runner.py` (+200), three new test files, deploy trigger.
- Variables: none changed. Stays disarmed (`L5_BTC5M_LIVE=""`, `L5_SHADOW_ONLY=1`, expired envelope).
- Tests: 3,205 lane + safety tests pass (3 unrelated Gemini files fail to import in this container on both commits).
- Acceptance after deploy (disarmed):
  1. boot shows `L5_BTC5M_ARM armed:false`, `L5_BTC5M_RESIDENT_DISARMED can_submit:false`;
  2. `L5_BTC5M_LAST_TRADED_WINDOW_ORDERS` prints 37 rows for cpc-btc-updown-5m-2026-11-21-0620z with
     `cancel_requested` per order and `ended_without_our_cancel` (expected 13 ids);
  3. watchdog reads the lane registry OK, 0 open orders.

## 2. l5-pmus-live
- Branch `release/l5-pmus-live-sibling-ledger-20261121`, commit `7c044c6da337fcefca35814b682915ee6ef4cd7b`
- Currently deployed: `03b77157115ae80e088aa01c0dba06f3a71fffb4`
- Diff: `src/l5_live_execution.py` (+164), `src/l5_venue_cost_attestation.py` (+148), one new test file (24 tests).
- Variables to ADD (two):
  - `L5_BTC5M_FILLS_URL=http://l5-btc5m-live.railway.internal:8090/btc5m_fills`
  - `L5_SIBLING_LEDGER_ATTESTATION=VENUE_FILLS_AND_SIBLING_LEDGER`
- Tests: full suite 21,549 passed / 72 failed / 26 errors. The same 72 failures and the same errors occur on the deployed
  commit 03b771571 in this container (missing `httpx` and similar after a container restart; all in Gemini, orderbook,
  spread-scanner, auth-signature and lineage tests). The earlier baseline on a complete container was 15,206 / 19.
  Zero failures in ledger, attestation, venue-basis, reconciliation or safety tests.
- Side effect of `L5_BTC5M_FILLS_URL`: it also enables the existing pre-settlement `attest_other_strategy_cost`
  (logs `L5_OTHER_STRATEGY_COST`). It requires fills to equal the ledger's held position per side exactly, so for a
  two-sided window it refuses and changes nothing.
- Acceptance after deploy:
  1. within ~10 minutes an `L5_VENUE_COST_ATTESTATION` line for cpc-btc-updown-5m-2026-11-21-0620z with
     `attested:true`, form `VENUE_FILLS_EQUAL_SIBLING_LEDGER`, old 0, new 0.0195, delta 0.0195;
  2. lifetime realized moves -39.4945 -> -39.4750 and nothing else moves;
  3. the market leaves the uncertain / unknown-basis lists;
  4. account ledger vs lane vs cash for the window all read +0.0195 (difference 0 <= 0.005).
  If the attestation refuses, the row stays at 0.00 exactly as now and the reason is logged; no live window follows.

## Order
Lane first (the account service reads the lane's feed), then the account service with its two variables.

## Rollback
Reconnect each service to its current commit above; remove the two variables. A correction already applied is a
recorded ledger restatement (`cost_basis_corrections`) and is not undone by rollback.
