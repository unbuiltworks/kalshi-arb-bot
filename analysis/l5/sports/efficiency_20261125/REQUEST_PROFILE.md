# Signed-request profile: sports controller (branch rn1-profit-first, 6a9a3e20a)

Read-only analysis; no tests run. File = src/pmus_sports_two_sided.py unless named. V = verified in code, I = inferred.

## 1. Signed request paths (V)
| Path | Class | Cadence |
|---|---|---|
| Fill detection: `step_market` 5264-5268, one `GET /v1/order/{id}` per open order (`Venue.status` 2261) | critical | every pass, every open order. Pass = LOOP_S (default 5 s, :91) + pass duration |
| `cancel_fast` 2298: 1 POST + 1..12 status reads (0.1 s apart); used by `_cancel_legs` 2478 (RN1 reconcile 5226, kickoff 4870, pulls 2519, orphan 6556) | critical | per cancel, >= 2 |
| `cancel_exact` (q1_v4:295): pre-read + POST + 1..10 reads, then a further `status` | critical | >= 4 per cancel; `_cancel_all_own` 3851, abandoned 5283, spec-gone 5312, legacy paths |
| Place: `micro.place_limit_buy` (pmus_micro_executor.py:363, 394) = POST preview + POST orders | normal | 2 per placement |
| `read_account` 3448 -> `Venue.account` 2211: positions page 1 + orders/open | normal | 2 per 20 s (6752-6758); also 4385 |
| `orphan_sweep` 6495 | none when account read <= 30 s old (6807); else 1 normal | 30 s |
| `positions_snapshot` 2233 (full walk + orders/open) | safety in audit (6322); normal from `_venue_net_complete` 3551 | hourly; per mismatch / per due reservation |
| Audit walk `activity_pages` 6286, reward read 6567, balances 6090 | safety / safety / normal | REWARD_CHECK_S 3600 |
| Research | research | side threads (combo, RFQ) |

Unsigned (V): bbo, book/depth, spec, discovery, settle lookups, the pre-place bbo all use the gateway base, which `GovernedTransport` skips (pmus_request_budget.py:226). Websocket (pmus_two_sided_ws.py:34-37): market data only, "no order path". No user order/fill channel exists in the repo; the recorder knows a public trade subscription (type 3).

## 2. Model (per minute; P = passes/min, R = open orders, N = placements/min)
- critical = P*R + ~2.2*N
- normal = 6 + 2*N; safety ~2.5; research ~1.5 (both roughly independent of M)

Observed 2669/70 = 38.1 critical/min. With R = 6, N = 1.08: P*R = 35.8, so P ~ 6 (10 s cycle, I: LOOP_S 5 + ~5 s pass; deployed LOOP_S not in repo). Normal predicts 8.2/min = 573 vs 550 sent + 44 deferred. Total 50.6/min = 0.84 rps, matching sent_rps. Per-order status polling is ~94% of critical and ~70% of everything.

## 3-4. Changes, ranked, with requests/min (rps) at constant P = 6
| | 4 | 8 | 12 | 16 |
|---|---|---|---|---|
| Today (9.6M + 0.54M + 10) | 51 (0.84) | 91 (1.52) | 132 (2.2) | 172 (2.9) |
| A | 18 (0.30) | 23 (0.38) | 28 (0.46) | 33 (0.55) |
| A+B (half the re-posts) | 16 | 18 | 21 | 24 (0.40) |
| A+B+C | 15 | 17 | 19 | 22 (0.36) |
| E alone | 33 | 55 (0.92) | 78 (1.3) | 100 (1.7) |

Today critical alone exceeds 1.0 rps at 8 markets; critical is never refused, so placements, account reads and audits would starve.

**A. One `GET /v1/orders/open` per pass; exact status GET only for an own order absent from the list or whose fill quantity changed.** Change `step_market` 5264-5278 (batch it in `run`/`service_pass`), `Venue.account`, `classify` (the list must become critical or it can be deferred; on a failed list read fall back to per-order polls). Preserves: fills still booked only from the exact order read (`status_from`, pending-cancel stays open); orphan sweep and reservations get a fresher list. Latency unchanged (one pass). Not verified: that list rows carry cumQuantity, that the list is unpaginated on the shared account, and its lag after accept (absent -> exact read, so safe). Tests: test_pmus_sports_order_state.py, _rate_limits.py, _safety_latch.py, _two_sided.py, _fills_feed.py, test_pmus_request_budget.py.

**B. Fewer cancel + identical re-post cycles** (4.2 requests each). `_rn1_step`: no_fair pulls 5153-5176 (RN1_GAP_HOLD_S default 0, :1283; live note says 444 of 1,111 decisions); reprice at exactly 1 tick (5211, REPRICE_TICKS 1; RN1_KEEP_DEEPER_TICKS 0); thin-touch flicker 5160; qty shrink 5217; vol/jump pause 5104-5123; account-risk 5082; pilot/audit staleness 5084-5089; kickoff 4868. The first four are churn, already switchable; pauses, account-risk and kickoff are deliberate pulls and should stay. Saving fraction is I. Tests: test_pmus_sports_gap_hold.py, _continuous_controller.py, test_pmus_rn1_replica.py, test_pmus_rn1_profit_first.py.

**C. Skip the preview** when the same slug/side/tif previewed clean recently: 1 normal per placement. Removes a per-order venue check; owner decision. Tests: test_pmus_micro_executor.py.

**D. `cancel_fast` for lock/shutdown/abandon cancels**: 2 instead of >= 4 each (96 -> 48 burst at 24 orders). Tests: test_pmus_sports_window_lock.py, _durable_halt.py.

**E. Poll deep orders every third pass, woken by the socket**: raises worst-case fill latency for deep bids to ~30 s; insufficient past 8 markets.

## 5. Unused or duplicate requests (V)
- `cancel_exact` pre-read only fetches the slug the bot already has; the `status` after it repeats its last confirm read (3851-3852, 5283-5284, 5312-5313, 5563-5564).
- `_venue_net_complete` 3555 pays for orders/open and discards it; it re-walks all positions once per due reservation (4110).
- `account()` re-reads orders/open every 20 s only for orphan/foreign checks; A makes that read do the fill detection too.
- `safety_check` -> `refresh_fills` 3615/3629 polls orders `step_market` re-polls seconds later.
- Preview result is used only for a slug/tif comparison and a log line.
