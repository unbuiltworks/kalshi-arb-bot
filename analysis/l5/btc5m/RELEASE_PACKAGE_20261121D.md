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
- Branch `release/l5-pmus-live-sibling-ledger-20261121`, commit `62b54e14a4a2db0f85d8133020a5ce5c47bb8194` (source identical to 7c044c6da; adds 13 interaction tests)
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

## Interaction check: what `L5_BTC5M_FILLS_URL` switches on before settlement
`Runner.attest_other_strategy_cost` runs once, just before a terminal 5-minute market is retired, only if the ledger
holds that market at the $1 bound. It GETs the lane's feed, GETs `/v1/order/{id}` for each filled order (17 for
window 1), and accepts only if every order agrees with the venue row AND the fills equal the ledger's held quantity
on each side exactly. Verified in `tests/test_l5_fills_url_interaction.py` (13 tests):
- window-1 shape with the ledger holding DOWN 0.10, 0.12, 0.17, 0.61 or UP 0.49: refused
  `FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION`; ledger state and file byte-identical before and after;
- lane unreachable, venue read failing (e.g. 429), feed not readable, an order with no venue id: refused, nothing written;
- it calls only `transport.order`, and only for filled orders;
- end to end with both settings: refused before settlement, retired at the bound (0.00), corrected to +0.0195 after;
- the one shape it accepts (ledger holds exactly the lane's fills per side) writes the fills' real cost.
Not covered by a test: its cost in time. It makes up to one 3 s feed read plus one venue read per filled order inside
the settlement sweep, once per market.

## Order
Lane first (the account service reads the lane's feed), then the account service with its two variables.

## Rollback
Reconnect each service to its current commit above; remove the two variables. A correction already applied is a
recorded ledger restatement (`cost_basis_corrections`) and is not undone by rollback.

## Deployed 2026-11-21 (owner-authorized; both services disarmed) — acceptance results

### l5-btc5m-live — deployment 82d7edb0, commit 5424a3244, 13:22Z — PASS
- Boot: `armed:false`, `can_submit:false`, stored state MONITOR, no submissions, no errors.
- Watchdog: lane registry OK in every sweep across the redeploy; 0 open orders; no cancels.
- `L5_BTC5M_LAST_TRADED_WINDOW_ORDERS` for cpc-btc-updown-5m-2026-11-21-0620z: 37 orders =
  17 FILLED, 6 CANCELED after the lane's cancel, 14 REJECTED by the venue (13 with no cancel of ours; 1 — DOWN 0.06 at
  0.75 — rejected, the lane's cancel at 06:22:40 found it already ended). No partial fills. Fees 0.0000 on all 37.
  UP 0.49 / 0.2948, DOWN 0.61 / 0.2957. Reject reason not stored (old code); post-only-would-cross is an inference.

### l5-pmus-live — deployment 7ee2e8bc, commit 62b54e14a, 13:27Z, two variables added — PASS
- Started disarmed (shadow lock, submit pause), 0 live orders, no tracebacks.
- 13:44:32Z `L5_VENUE_COST_ATTESTATION`: cpc-btc-updown-5m-2026-11-21-0620z attested, form
  `VENUE_FILLS_EQUAL_SIBLING_LEDGER`, 0.00 -> 0.0195, delta +0.0195.
- Lifetime realized -39.4945 -> -39.4750 (heartbeat, lifetime basis and all-time figure all agree); UTC day +0.0195.
- 0620z gone from `unknown_basis_keys` and from `uncertain_markets` (now empty).
- Lane +0.0195, account ledger +0.0195, cash +0.0195: difference 0.
- Also re-read by the new form and REFUSED, nothing changed: 5m markets 11-13-1605z, 11-13-2215z, 11-13-2255z,
  11-15-1925z (`SETTLED_BEFORE_THE_LAST_REBASE`; 1 of 3 attempts used each).
- `L5_OTHER_STRATEGY_COST`: 0 lines (no 5-minute market reached settlement since the deploy), so the pre-settlement
  check has still not run in production.
- Feed walk completed without hitting the page cap.
