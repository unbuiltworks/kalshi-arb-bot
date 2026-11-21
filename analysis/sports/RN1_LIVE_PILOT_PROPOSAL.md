# RN1 replica — small-capital live pilot proposal (for owner approval; nothing armed)

Prepared 2026-11-21 by the sports production coordinator. Step A (disarmed) runs `920814fb4`.

## Preconditions (must all hold before the lock is lifted)

1. **Pilot release deployed disarmed first.** The pilot runs the dev commit that adds the
   stale-account completion constraint (venue-confirmed inventory/order state). It is a new
   SHA: it gets its own release branch, is deployed with the lock ON, verified, and only then
   is the lock lifted. Two separate approvals.
2. **24 h of Step A with no `http_429` growth on account reads** (heartbeat `venue_requests`,
   `account_read_error`), no `cancel_error`, watchdog clean.
3. **Settled-P&L meter** (`src/pmus_pilot_pnl.py`, logged hourly as `PILOT_PNL`): a pilot market
   counts only when the venue's trade-row quantity equals the book's fills, the venue shows a
   position resolution, and the final price is known. The venue's `realizedPnl` is blank on
   most rows, so it is not used.

## Markets

- RN1 mode's existing selector, live in-game moneylines only, sports allow-list
  `cfb, nba, wta, cs2` (open-EV gate on).
- International guard REQUIRED: a market with no fresh, matched international price gets no
  opening/extension. In practice this limits the pilot to markets listed on both venues
  (CFB, NBA, tennis today; most CS2 has no international listing and is blocked).
- **2 markets at a time** (`PMUS_TWO_SIDED_MAX_MARKETS=2`, down from 3), one at a time if you
  prefer the stricter start.

## Order sizes and exposure

| Limit | Value | Bound |
|---|---|---|
| Order size | 5 contracts (`PMUS_RN1_QTY=5`) | ~$1–4.60 per order |
| Spend per market | $8 (`PMUS_RN1_PER_MARKET_USD`) | hard cap |
| Unpaired cost per market | $3 (`PMUS_RN1_RESIDUAL_CAP_USD`) | hard cap |
| Pair cost incl. fees | ≤ $0.97 (`PMUS_RN1_PAIR_CAP`), simultaneous bids included | |
| **Total spend at once** | **2 × $8 = $16** | below the account lane cap of $20 |
| **Total unpaired at once** | **2 × $3 = $6** | |
| Order type | post-only BUY only; taker off; nothing sells | |

## Loss limits

- **Daily:** openings stop at −$5 (`PMUS_TWO_SIDED_DAILY_LOSS_USD=5`, unchanged); completions continue.
- **Campaign (in code):** `PMUS_RN1_PILOT_LOSS_USD=4` — when the meter's venue-settled pilot net
  reaches −$4, new RN1 openings/extensions stop and the stop is **latched in the book** (a restart
  does not lift it; only you clear it). Completions continue. The coordinator also re-locks.
- Account-wide: existing shared halt and account ledger (daily halt $50, loss room ~$35) unchanged.

## Duration (owner 2026-11-21: 24 hours first)

**24 hours** from the unlock (`PMUS_RN1_PILOT_SINCE` = unlock time), then the coordinator
re-locks. Positions opened in the window are held to settlement and measured. After the first
settled markets we review execution quality and venue-settled P&L together before any extension.
No automatic extension; a crash or restart does not re-arm anything beyond the lock state you set.

## Guard behaviour

- Openings and extensions: need a fresh (≤20 s) international price that does not say our
  bid is rich; otherwise no order (`intl_guard:*` in `RN1_QUOTE`). Fail-closed.
- Completions are exempt from the guard (they reduce exposure) but must be at ≤ $0.97 pair.
- Stale account (> 60 s since a good read): no openings/extensions; a completion goes only
  after a fresh read confirms venue position == book, the completion reduces the venue
  imbalance without flipping it, and venue open orders == book open orders.

## Cancellation safeguards

- Exact-order-id cancels only; no bulk/market cancel (manual orders untouched).
- Cancels and order-status reads are first in the shared request budget, never deferred.
- A failed cancel keeps the order open in the book and the watchdog registry until the venue
  confirms it closed; shutdown retries each cancel (4 attempts / 20 s) and reports any it
  could not cancel.
- Watchdog: cancels any of our orders older than 300 s it cannot match, independent of the bot.

## Stop rules (coordinator re-locks immediately)

1. Campaign settled net ≤ −$10.
2. Any book/venue inventory mismatch, or any `cancel_error` with `still_open`.
3. Any order or production change not explained by the approved config.
4. 24 hours elapsed.

## How settled net P&L is measured

Per pilot market, only when fills reconcile and the venue shows it resolved:
`gross = YES qty × px + NO qty × (1 − px) − cost`; `net = gross − fees charged on our side`.
`gross` splits into matched-pair edge and the unpaired (residual) leg's outcome. A market whose
venue trades do not match the book is reported as `fills_unconfirmed` and left out. Reported separately: matched-pair edge, residual (unpaired) P&L,
fees, and credited rewards (never mixed into trading P&L). Labels: submitted → filled →
settled → realized. Nothing unsettled is called profit.

## What the pilot can show

At ~$16 of capital, an excellent result is a few dollars a week. The pilot tests whether the
decision logic makes money net of fees at our size and whether execution stays clean; it is
not a profit target. RN1's own small positions lost money (−10.8%), so a negative result is
plausible and would be an answer, not a failure of the test.

## Approvals needed, in order

1. Deploy the meter release disarmed (lock on). Done for 3f65fc99b (stale-completion fix);
   the meter needs one more disarmed release.
2. After verification and 24 h of stable Step A: one change set —
   `PMUS_TWO_SIDED_SUBMISSION_LOCK=0`, `PMUS_TWO_SIDED_MAX_MARKETS=2`,
   `PMUS_RN1_PILOT_SINCE=<unlock time>`, `PMUS_RN1_PILOT_LOSS_USD=4` — for 24 hours.
