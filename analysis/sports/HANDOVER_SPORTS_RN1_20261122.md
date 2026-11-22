# Sports bot (pmus-spring-l5-probe) — RN1 handover, 2026-11-22 ~03:10Z

From the session that ran the RN1 pilot (session_01HTk9273etZreZcZBzhrUyv). Written for whichever
session the owner confirms as production owner. No production change accompanies this file.

## 1. Production state now

| Item | Value |
|---|---|
| Service | `pmus-spring-l5-probe` (d84d9a68-2e5b-4f74-9694-5f422417c74f), env production f3642d27… |
| Running | `3e2cc9002` (deployment `1bafc4f9`, since 2026-11-22T01:55:27Z), branch `release/pmus-sports-rn1-3e2cc9002`, commitSha pinned |
| Lock | `PMUS_TWO_SIDED_SUBMISSION_LOCK=1` — every order refused (`place_refused_submission_lock`) |
| Orders / positions | watchdog open_orders 0; venue positions 0 (both pilot markets closed flat by netting) |
| Watchdog | `fb038b8c…` healthy; registry self-test OK |

Release process: `deploy/pmus_spring_l5/RELEASE_PROCESS.md` — one release branch per approved SHA
(`release/pmus-sports-rn1-<sha9>`), connect-service-source with branch + commitSha, redeploy the
SKIPPED deployment id (watch pattern), verify the deployment's `meta.commitHash`.

## 2. Undeployed work (branch `dev/pmus-rn1-replica`)

| SHA | Change vs running 3e2cc9002 |
|---|---|
| `5658476e9` | `RN1.committed()` exact worst case over which resting bids fill. 3e2cc9002 UNDERCOUNTS the completion reserve of a resting extension (YES 2 filled @0.44 + 2 resting @0.44: true $3.88, counted $2.82), so campaign caps could be exceeded. |
| `5e4f153c2` | Two-markets-maximum for RN1 new exposure (3e2cc9002 bid openings in 3 markets at once with MAX_MARKETS=2 — seen live, locked, 01:59Z). Completions never blocked. |

Tests: `python3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors tests` on the sparse
worktree → 209 failed / 4599 passed / 10 errors; the failure set is identical to the deployed base
(all pre-existing, mostly missing modules in the sparse checkout). RN1-specific files all pass:
`tests/test_pmus_rn1_accumulation.py`, `test_pmus_rn1_replica.py`, `test_pmus_sports_order_state.py`,
`test_pmus_pilot_pnl.py`, `test_pmus_sports_rate_limits.py`, `test_pmus_sports_intl_guard.py`,
`test_pmus_request_budget.py` (105+ pass).

Status: `5e4f153c2` awaits owner approval for a DISARMED deploy. Not approved yet.

## 3. Variables set/changed on the service (21 Nov ~22:00Z onward) — names and non-secret values

| Variable | Value | Intent |
|---|---|---|
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `1` (was `0` only 01:15–01:29Z on 22 Nov, owner-approved pilot) | master order lock |
| `PMUS_TWO_SIDED_INTL_GUARD_REQUIRE` | `1` | international guard fail-closed |
| `PMUS_TWO_SIDED_TAPE_KEEP_DAYS` | `30` | order-book tape retention |
| `PMUS_TWO_SIDED_COMBO_STUDY` | `0` | research combo study off (API budget) |
| `PMUS_TWO_SIDED_MAX_MARKETS` | `2` | markets (see defect: only bounds selection on 3e2cc9002) |
| `PMUS_RN1_PILOT_LOSS_USD` | `4` | campaign loss stop (latched) + fail-closed meter gate |
| `PMUS_RN1_PILOT_SINCE` | `2026-11-22T01:15:00Z` | pilot market set — STALE; set a new value at the next unlock |
| `PMUS_TWO_SIDED_OPEN_OFFSET_PX` | `0` | owner-approved 01:24Z: legacy reactive pull no longer cancels RN1 1-tick bids |
| `PMUS_TWO_SIDED_DEPTH_TICKS` | `1` | same |
| `PMUS_RN1_QTY` | `2` | 2-contract orders (env overrides code default) |
| `PMUS_TWO_SIDED_RN1` + `PMUS_RN1_*` (set with the ca8b878bb Step A, 21 Nov) | RN1=1, DEPTH_TICKS 1, PX_MIN 0.20, PX_MAX 0.92, PAIR_CAP 0.97, RESIDUAL_CAP_USD 3, PER_MARKET_USD 8, EXTEND_MAX_UP 0.02, REFILL_COOLDOWN_S 60, REPRICE_TICKS 1 | RN1 replica settings |
| `PMUS_TWO_SIDED_REWARD_FIRST` / `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` (same Step A) | `0` / `cfb,cs2,nba,nfl,wta` | RN1 market selection |

Campaign caps come from code defaults: `PMUS_RN1_TOTAL_SPEND_USD` 16, `PMUS_RN1_TOTAL_UNMATCHED_USD` 6
(not set as variables). Daily halt `PMUS_TWO_SIDED_DAILY_LOSS_USD=5` unchanged. Taker off, combo maker
shadow — unchanged.

## 4. Gates and authorizations

Order path gates (place()): shutdown → stale-account (completions need venue-confirmed inventory) →
account-risk (observe) → SUBMISSION_LOCK → shared halt. RN1 new exposure additionally: enabled,
total cap, daily loss stop, halt_new_exposure, shared halt, account risk, pilot gate
(`pilot_meter_unavailable` / `pilot_fills_unconfirmed` / `pilot_loss_at_risk` / latched
`pilot_loss_stop`), late/excluded, open-EV gate, jump pause, intl guard, (5e4f153c2: max_markets),
campaign caps, per-market caps.

Owner HAS authorized: disarmed deploys of each released SHA; the 2-hour pilot window 01:15–03:00Z on
22 Nov (ended; re-locked 01:29Z on a stop rule). Owner has NOT authorized: any new live window,
lifting the lock, taker execution, capital increase, BTC trading, crypto arming.
Limits the owner set: $16 campaign spend, $6 unmatched, $8 / $3 per market, 2 markets, 2-lot,
0.97 pair cap incl. fees, $4 campaign stop, $5 daily halt, maker-only, intl guard fail-closed.

## 5. Pilot result (22 Nov 01:15–01:29Z, 38582bfa2)

4 fills: UTSA/UAB NO 5@0.35 + YES 5@0.62 (0.97); LAC/DAL NO 5@0.57 + YES 5@0.39 (0.96).
Venue cash (l5-pmus-live per-minute equity): 321.3138 → 321.6638 = **+$0.3500**, fees $0.
Netting closed both markets at trade time; no POSITION_RESOLUTION will follow.
Analysis: `analysis/l5/sports/PILOT_20261122_CASH_RECONCILIATION.md` (research branch).

## 6. Known defects / in-flight

1. Pilot meter waits for POSITION_RESOLUTION; venue-flat (netted) pairs are never "settled" → add a
   venue-flat + reconciled-fills realized basis, from venue trade cash.
2. Bot logs no venue cash; add a balance read to ACCOUNT_AUDIT.
3. Fill detection lagged ~60 s across a restart (LAC/DAL YES fill) — don't restart mid-pilot.
4. Shared account: l5-pmus-live preflight `no_unexpected_open_orders` fails while sports orders rest.
5. RN1 handles every market incl. crypto if `PMUS_TWO_SIDED_CRYPTO_PREFIXES` is set; today blocked by
   the open-EV gate (negative all-sport EV) and the intl guard (no intl match) — exclude explicitly.
6. 3e2cc9002 defects fixed on dev: campaign-commitment undercount (5658476e9), 3-market openings
   (5e4f153c2).
7. Account-read 429s recur (shared key); freshness gate held.

## 7. Scheduled tasks of this session touching the service (all read-only)

- `trig_015sPKRMW42QjTiypZMqi8yf` — 04:30Z 22 Nov reconciliation (read-only).
- `trig_018dMQwzER9ixTcKvbJErVSB` — 00:32Z 23 Nov stability review (read-only; obsolete).
- Fired/expired: pilot checkpoints and the 03:00Z end reminder.
No watchers or monitors write to the service.
