# READY_ON_RECOVERY — PMUS probe, automatic sequence (OWNER 5554553793 A)

One page. No manual step exists between "venue prints again" and "R1
campaign continues". Everything below is code on `feature/l5-transfer-backtest`
running as Railway service `pmus-spring-l5-probe` (restart policy NEVER,
durable mount `/state/pmus-spring`).

## State that survives a restart or redeploy

| state | where | proven by |
|---|---|---|
| ledger (submitted → filled → settled → realized) | `/state/pmus-spring/ledger.jsonl` | `test_held_inventory_is_rebuilt_from_the_ledger`, `test_resume_reconciles_an_unterminated_fill_before_rebuilding` |
| held-market lock | venue positions (authoritative) + ledger | `test_campaign_resumes_the_held_market_and_starts_no_other`, `test_resume_refuses_when_ledger_and_venue_disagree` |
| outage baseline (sharesTraded per liveness market) | `/state/pmus-spring/l5_campaign_state.json` | `test_outage_baseline_and_gate_survive_a_restart` |
| recovery gate (pending / checks) | same file | same test (run 2 keeps waiting; run 3 resumes only past the ORIGINAL baseline) |
| recovery-burst milestones already reported | same file (`burst.done`, wall-clock start) | `test_recovery_burst_milestones_are_not_double_counted_after_restart` |
| R1 rung: filled and settled markets | same file | `test_rung_fills_survive_a_restart` (2 before restart + 1 after → `CAMPAIGN_RUNG_COMPLETE`) |
| daily realized loss stop | ledger (`realized_today`) | existing q1 tests |

The file is written atomically at every transition (outage start, account
recovery, gate open, burst milestone, fill, settlement) and read once at
boot; a missing or unreadable file reads as empty and the boot gate below
still applies.

## Automatic sequence on the next venue print

1. **Boot / running, tape frozen.** If no outage is persisted and the
   recorder tape shows no trades, the boot gate samples the baseline and
   waits (`boot_gate_waiting`). A persisted outage restores its baseline
   and gate (`campaign_state_restored`). Account reads healthy alone is
   NOT enough: status `CAMPAIGN_WAITING:VENUE_PRINTS`, one line every 4
   checks, no session, no order.
2. **A print lands** (sharesTraded above the baseline on any liveness
   market, or trades on the recorder tape) → `venue_trading_resumed`
   with the postmortem stamps (start, first error, account recovered,
   trades resumed, resumed-by). Gate cleared and persisted.
3. **Re-sweep / reconcile** already happened at the first healthy account
   read (`venue_recovered`: open orders in our markets cancelled by exact
   id, any foreign open order → `CAMPAIGN_STOP:FOREIGN_OPEN_ORDERS`).
4. **Held-state decision.** The held market (currently
   `asc-cfb-ntx-ind-2026-09-05-pos-39pt5`, +0.06 YES) is resumed with its
   ledger-rebuilt inventory: the v2 model decides EXTEND / COMPLETE / HOLD
   on the real residual; residual cap 3 units; off-distribution → HOLD;
   complement ≤0.98 and ≥1c after known fees; maker/post-only; one
   outstanding order; rest ≤30s with cancel on touch move.
5. **Settlement.** When the venue drops the market from positions, the
   settlement audit records realized ONLY from `afterPosition.realized`;
   the market counts toward the rung.
6. **Campaign continues** to the next R1 market (3 markets, $0.05 filled
   each, $0.15 worst case, −$1.00 daily stop) and stops at the advance
   gate (`CAMPAIGN_RUNG_COMPLETE`) with the ladder's PROMOTE/HOLD/ROLLBACK
   evidence. Promotion is an owner env change only.
7. **Recovery-burst reports** at 30/60/120 minutes after resume
   (`recovery_burst`), persisted so a restart cannot re-emit a milestone;
   a post-restart report is labeled as counting only that process.

Ambiguity fails closed at every step: two markets held →
`CAMPAIGN_STOP:MULTIPLE_POSITIONS`; ledger/venue disagreement → refusal;
foreign open order → stop; venue 5xx → logged wait, never an order.
Deterministic end-to-end simulation: `test_deterministic_recovery_simulation`.

## What is NOT changed (per the directive)

Cap-fallback COMPLETE stays off, taker disabled, per-market cap $0.05,
concurrency 1, rung R1. No strategy or risk change is in this pass.

## Recovery checklist — live evidence 2026-09-06 00:28Z (OWNER 5555609705 item 4, READ-ONLY)

| check | evidence | result |
|---|---|---|
| recorder health | build 39dc163a, connected, auth ok, 128 seats, 29.5k messages; `trades_1h` 0–13 on watched rows; last authenticated book update ~5850 s old (venue quiet, not a recorder fault) | PASS (venue not live) |
| supervisor (probe) health | build 44d63132 (commit 92d8e8f4), ARMED, flat, selecting every ~20 s (143 cycles by 00:28Z), no error lines since 22:48Z | PASS |
| durable state | ledger rows `settled` + `realized` written at 23:51:29Z; campaign state file persists filled/settled markets (tested restart paths) | PASS |
| kill/arm state | dual key present (mode ARMED at boot); restart policy NEVER; nothing can arm from code | PASS |
| credentials / account reads | positions, open orders, activities and settlement audit all succeeded after 22:48Z | PASS |
| open-order sweep path | boot sweep 0 seen; post-recovery re-sweep 0 seen; foreign order → stop | PASS |
| full sports-universe discovery | 128 rows per selection cycle from the recorder status (≤10 s old, `evidence_age_s` 2.7–9.2) | PASS |
| fresh-decision requirement | every cycle re-reads the status doc; strict gates (`thin_flow`, `market_closed`, band, in-distribution) refused all 128 rows since settlement — 0 admitted | PASS (no candidate while the venue is quiet) |
| caps | q = venue-minimum unit; $0.05 filled per market; R1 = 3 markets ($0.15); −$1.00 daily stop; residual cap 3; concurrency 1 (boot `rung` line) | PASS |
| complement retry / deadman | session `finally` cancel-all; SIGTERM → deadman; rest ≤30 s re-checked every poll (tests) | PASS |
| CFB residual settlement | venue POSITION_RESOLUTION 23:50:51Z, side LONG, beforeNet 0.0600 → afterNet 0; `afterPosition.realized` **$0.0000**; ledger rows written once | SETTLED / REALIZED $0.00 (venue) |
| cashflow reconciliation | a LONG resolution of 0.06 YES bought at $0.0292 would imply either +$0.0308 (won) or −$0.0292 (lost); the venue reports realized 0.0000 — consistent with a void/refund-at-cost, not yet confirmed by a cash row | OPEN (needs the venue's cash activity row; no action, no order) |
| campaign horizon | campaign_s 21600 from 20:41Z → the process exits at ~02:41Z with restart NEVER; a redeploy (boot gate + persisted state) restarts the lane safely | WATCH (redeploy at expiry if the venue is still quiet) |

No engineering work remains for the R1 path itself; the two open items are venue-side (cash row for the $0.00 realized) and operational (campaign expiry).
