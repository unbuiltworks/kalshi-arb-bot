# RN1 replica — small-capital live pilot proposal (for owner approval; nothing armed)

Prepared 2026-11-21 by the sports production coordinator. Step A (disarmed) runs `920814fb4`.

## Preconditions (must all hold before the lock is lifted)

1. **Pilot release deployed disarmed first.** The pilot runs the dev commit that adds the
   stale-account completion constraint (venue-confirmed inventory/order state). It is a new
   SHA: it gets its own release branch, is deployed with the lock ON, verified, and only then
   is the lock lifted. Two separate approvals.
2. **24 h of Step A with no `http_429` growth on account reads** (heartbeat `venue_requests`,
   `account_read_error`), no `cancel_error`, watchdog clean.
3. **Settled-P&L meter built (read-only)**: per pilot market, venue cash out (fills x price +
   fees, from venue trade rows) vs venue cash in (settlement / redemption rows), on the exact
   pilot market set. The venue's `realizedPnl` field is blank on most rows (reconciliation
   finding), so it is not used.

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
- **Campaign:** cumulative venue-settled net P&L ≤ −$10 → coordinator re-locks, no restart.
- Account-wide: existing shared halt and account ledger (daily halt $50, loss room ~$35) unchanged.

## Duration

7 days or 30 settled pilot markets, whichever is first, then stop and report for your decision.
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
4. 7 days / 30 settled markets.

## How settled net P&L is measured

Per pilot market, only after the venue shows the market resolved:
`settled net = settlement cash received − (Σ fill qty × price) − fees`, from venue rows, on
the pilot market set only. Reported separately: matched-pair edge, residual (unpaired) P&L,
fees, and credited rewards (never mixed into trading P&L). Labels: submitted → filled →
settled → realized. Nothing unsettled is called profit.

## What the pilot can show

At ~$16 of capital, an excellent result is a few dollars a week. The pilot tests whether the
decision logic makes money net of fees at our size and whether execution stays clean; it is
not a profit target. RN1's own small positions lost money (−10.8%), so a negative result is
plausible and would be an answer, not a failure of the test.

## Approvals needed, in order

1. Deploy pilot release `<sha>` disarmed (lock on), settings above. 
2. After verification and 24 h of Step A: set `PMUS_TWO_SIDED_SUBMISSION_LOCK=0` for the pilot.
