# Campaign D — one event, full lifecycle, two-sided accumulation (for authorization)

26 Nov 2026. Nothing deployed, armed or changed. It follows the ledger
release in EQUITY_RECONCILIATION_20261126.md, and rearming requires that
release to be verified first.

## Preconditions (each verified and reported before the window opens)

1. Ledger release deployed. The UConn row is restated from −469.00 to
   −20.63 with its correction record, NHL is restated to its venue result,
   and no unresolved bound row remains. Any that do are named to the owner.
2. Today's and all-time realized agree with the venue bridge to within its
   stated gaps. The equity drawdown is unchanged (venue cash against anchor
   312.4102). The published account-risk record is readable and no halt is
   in force.
3. The sports bot is on release `1b496656c` with the Campaign C settings,
   lock `1`, zero open orders and a clean watchdog.

## The event

- **Chosen at the window start, once:** the reward-eligible event with the
  highest positive net estimate (the bot's own ranking) whose scheduled
  start is 10–60 minutes ahead and whose expected finish is inside the
  window. Set by `PMUS_RN1_PIN=<that market>`; the market is named to the
  owner when it is set.
- **Duration:** table tennis is about 30–45 minutes and tennis doubles
  about 60–90. The window is sized to the chosen event.
- If no event qualifies at the start, nothing opens and the owner is told.

## How the event is traded (Campaign C settings, unchanged)

- **Both sides:** YES and NO ladders of 3 levels, 2 ticks apart, 1 or 2
  contracts each, chosen by the net estimate.
- **Replenishment:** 10 seconds after each fill, re-priced on the new book
  first.
- **Accumulation:** repeated same-side buys and uneven inventory up to the
  $5 per-market unpaired cap and the fit rule; completions are allowed but
  never forced. Accumulation continues after pairs complete.
- **Resting bids are kept** through a brief non-positive estimate (300 s
  hold), unless a bid would become the best price on its side.
- **Through the start:** opening bids are withdrawn 5 minutes before it and
  resume in play once the match is live and calm. The event keeps its slot.
- **Leaving the event:** only after 10 minutes without a positive
  estimate, or when a block applies (late, ended, cap, lock).

## Limits (unchanged)

- **Account:** limit $20 (equity drawdown).
- **Campaign:** loss stop $10, counting resting bids; daily halt $10.
- **Exposure:** sports lane $20; unpaired $5 per market and $10 for the
  campaign; $12 per market; spend $60; at-risk $20.
- **Execution:** maker-only, no taker. Esports off; crypto unchanged and
  disarmed.
- **Account-wide ceilings:** the ledger's daily $50 and cumulative $75 are
  unchanged. After the fix the lifetime figure is about −61; check the
  ceiling's room before the window.

## Reported at the end

- YES and NO contracts accumulated, and the weighted average price on each
  side.
- Resting ladder levels over time.
- Time from each fill to the next bid on that side.
- Same-side and opposite-side buys.
- Percentage of event time with both sides quoted.
- Capital in use against room.
- Reward-qualifying time.
- Fills with markouts.
- Settled P&L (bot only), confirmed rewards, unexplained cash.

## Authorization line (after preconditions 1–3 are reported)

> Authorized: single-event campaign D on release 1b496656c with the
> Campaign C settings and all limits unchanged; window from ____ UTC to
> ____ UTC 26 Nov sized to the chosen event, with a two-hour completions-only
> grace; event chosen at the window start as written in
> CAMPAIGN_D_SINGLE_EVENT_20261126.md and pinned; maker-only; esports off;
> crypto unchanged and disarmed.
