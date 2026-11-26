# Account ledger vs venue — reconciliation and permanent fix (26 Nov 2026)

Trading is locked (`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`, 03:19 UTC); crypto is
disarmed. Nothing below has been deployed.

## 1. Venue figures (the source of truth), 03:30 UTC

| | Venue |
|---|---|
| Cash / buying power (`/v1/account/balances`, read by the ledger; the sports bot's own read agrees) | **$308.7235** |
| Open positions | UTR `garcar-borvic` 4 YES (bot, cost $2.60); Setka `slomyk-fasiva` 2.18 YES net (bot). No manual position open |
| Open orders | 0 |
| Account value, conservative (open positions valued at zero) | $308.72 |
| Account value, marked (not realized) | about $311.4 |
| Venue-confirmed result of the owner's UConn trade | −$20.63 (cash −25.00 at 02:08:56, +4.37 at 02:14:22) |

## 2. The ledger against the venue

| Figure | Ledger | Venue-consistent | Difference |
|---|---|---|---|
| All-time realized (`realized_today()`, the cumulative ceiling's input) | −514.9314 | −66.5614 (−45.9314 before the event + −20.63) | **−448.37** |
| UTC-day realized (the daily halt's input) | −470.09 | −21.72 | **−448.37** |
| Equity drawdown (anchor 312.4102 − buying power − guaranteed pairs) | 3.6867 | 3.6867 | 0 |

**The equity-based drawdown is correct.** It reads venue cash directly.
The two realized-loss figures are wrong by exactly $448.37: one row booked
−469.00 where the venue's result is −20.63.

## 3. Exact cause (timestamps from the ledger's own logs)

1. **02:07:50.** Owner buys 469 NO in `aec-cbb-duke-uconn-2026-11-25`
   (venue order `DYSS79F015P6`).
2. **02:12:22.** The ledger reconciles. The venue holds 469 NO and the
   ledger holds nothing, so it adopts the venue's 469 "over our book" and
   prices them at the **$1-a-share upper bound** ($469), because it never
   saw the price. A risk floor of 469 is recorded.
3. **02:14:01.** Owner sells all 469 (`DYSW1R1G15P6`). The venue position is
   now zero.
4. **02:17:45 onward.** The ledger logs drift: it holds 469, the venue holds
   0. Its stale copy is not cleared.
5. **02:26:26.** The market expires (YES = 1, so NO pays 0). The settlement
   sweep retires the stale book: redemption 469 × 0 − cost $469 =
   **−469.00**, provenance `UNKNOWN_COST_UPPER_BOUND`. `realized_today`
   drops to −514.93.
6. **02:44 and 03:10.** The existing venue-cost attestation, built to
   replace the $1 bound with the venue's own fill costs, refuses both times
   with `COST_CROSSCHECK_FAILED`. It reads every fill as a purchase, so the
   02:14 SALE (venue cost = proceeds) fails the per-fill check. After three
   attempts the bound is kept for good.
7. **03:17.** The ledger restarts for the authorized anchor change. The
   sports bot's account check now reads the stored figures:
   `ACCOUNT_DAILY_LOSS_HALT` (−471.06 against a $50 halt) and a cumulative
   ceiling breach (−515.90 against $75). The anchor change did not cause
   it: the ledger already showed `realized_today −514.93` at 02:48.

The owner's broader concern is accounting that books losses at the bound
and never credits the real result. That is this mechanism: any position
the ledger did not open is booked at $1 a share and must later be corrected
from the venue's fills. Before today the correction covered buy-and-hold
positions only. A position bought and sold before settlement could never be
corrected.

Profits, settlements, rewards, rebates and fees:
- **Settlements:** booked once per market. The retire step refuses a second
  pass, and the refusal persists across restarts.
- **Fees:** the venue's `realizedPnl` is fee-inclusive. This trade's cash
  movements (−25.00, +4.37) equal its realizedPnl (−20.63). Bot fees are
  reported separately in the sports account audit ($4.36 lifetime).
- **Rebates:** $0 credited to date.
- **Rewards:** credited outside the trading ledger and reported separately
  ($24.09 credited, `REWARD_CHECK`). They are not in either realized figure,
  so they are not double counted, and they do not offset trading losses.
- **Historical rows at the bound:** 122 rows (origin UNKNOWN, −588.12 in
  total, mostly before 16 Nov). The ledger's lifetime figure was re-based to
  venue cash on 16 Nov (cash anchor 338.3456; the residual is held as
  `realized_unattributed`), so pre-rebase bound losses do not distort the
  lifetime figure. Rows since 13 Nov are eligible for the venue-cost
  correction.

## 4. Permanent fix (ledger release, not deployed)

Code `0c134202a` on top of the deployed ledger `c70e244a2`. Files:
`src/l5_venue_cost_attestation.py`, `src/l5_live_execution.py`,
`tests/test_l5_venue_round_trip.py`.

**Round-trip form of the venue-cost correction.** A retired market still at
the $1 bound whose venue trades include a sale is read trade by trade:
- for each trade: the order's intent (BUY_LONG, SELL_LONG, BUY_SHORT,
  SELL_SHORT), our executed shares, the venue's `realizedPnl`, and the
  trade id;
- the form applies only when every row is readable and the venue's net
  position on both sides is exactly zero, meaning the position was closed
  before settlement.

The row's result then becomes the sum of the venue's per-trade
`realizedPnl`, kept on the day the market settled. The original row is
preserved, a correction record with the order and trade ids is appended,
realized moves by the delta, and the bound keys and risk floor are cleared.
A market whose every fill carries the venue's MANUAL indicator is labelled
MANUAL. That is a label only: the manual loss stays in total account P&L.

**Fail-closed cases, where the bound stays:**
- a position still open at settlement;
- any unreadable intent, shares or realizedPnl;
- a market this ledger traded itself;
- a trade id already used by another correction.

**Attempts:** counted separately and bounded (3). A market already
exhausted under the exact forms is read once more, for this form only.

**Restarts:** the correction is stored with its id and the corrected row in
the ledger's state file. A restart can neither repeat it nor undo it
(tested).

**On deployment:**
- The UConn row is restated from −469.00 to −20.63 at the next attestation
  pass, about 2–10 minutes after start.
- The daily figure becomes about −21.72. The halt ($50) and ceiling ($75)
  are then no longer tripped.
- Any other row since 13 Nov with a closed round trip is also restated to
  its venue result, each logged with evidence.

## 5. Not changed

- **Equity drawdown (anchor − venue cash − guaranteed pairs):** unchanged.
  It reads venue equity and is correct. Deposits and withdrawals are still
  applied as recorded anchor moves.
- **Venue-equity baseline with an automatic transfer feed:** it exists in
  the ledger but is not enabled (`EQUITY_TRANSFERS_NOT_ESTABLISHED`). Turning
  it on is a separate decision.
- **No limit changes:** daily halt $50, cumulative ceiling $75, account
  limit $20, and the bot's own $10 loss stop and $10 daily halt are all
  unchanged.
- **The bot/total split:** remains off.
- **One consequence to note.** The owner's manual loss stays in total
  account P&L, so after the fix the lifetime figure is about −66.56 against
  the $75 cumulative ceiling. That leaves about $8.44 of room, which is less
  than the campaign's $10 loss stop. The account ceiling would therefore
  bind first. Changing the ceiling is the owner's decision; nothing here
  changes it.

## 6. To rearm (each step needs the owner's approval)

1. Deploy the ledger release (sha below), locked.
2. Verify the UConn correction record, the day figure at about −21.72,
   lifetime at about −66.56, the equity drawdown unchanged, and no halt.
3. The owner approves rearming and a new campaign window.

## 7. Release and regression

- **Release:** `086380d88687b52ce81dcdf03e17284aed7c8c6d`, branch
  `release/l5-pmus-live-round-trip-086380d88` (code `0c134202a`, on the
  deployed `c70e244a2`, plus one deploy-trigger line). Code branch
  `dev/l5-ledger-round-trip-attest`.
- **New tests:** `tests/test_l5_venue_round_trip.py`, 12 tests. They cover:
  - the live UConn case (−469 → −20.63, day and lifetime move by exactly
    +448.37);
  - manual label only;
  - keys and risk floor cleared;
  - the evidence record;
  - position still open at settlement, unreadable intent and missing
    realizedPnl each keep the bound;
  - a market the ledger traded is not replaced;
  - attempts bounded;
  - a restart neither repeats nor undoes the correction;
  - a second application is refused;
  - an exhausted market with no sale is ruled out once.

  The existing attestation tests are unchanged and pass (41).
- **Full ledger suite:** 21,766 passed, 72 failed, 26 errors. The baseline
  `c70e244a2` gives 21,754 passed and the **same 72 failures and 26 errors
  by name** (`regression/ledger_full_round_trip.txt`,
  `regression/ledger_full_baseline_c70e244a2.txt`).
