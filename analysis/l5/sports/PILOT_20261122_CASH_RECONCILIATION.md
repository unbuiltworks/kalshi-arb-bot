# Sports pilot 22 Nov 2026 01:21-01:32Z - venue cash reconciliation

Read-only. Sports bot `pmus-spring-l5-probe` on `38582bfa2`, 5-contract lots.
Cash source: `l5-pmus-live` heartbeats, field `available_cash.usd` read from the
venue balances endpoint about once a minute (same account). Equity field and
15-minute buying-power probes agree at every reading.

| Venue cash | First seen (UTC) | Change | Fill that explains it |
|---|---|---|---|
| 321.3138 | 00:30 - 01:25 | - | (before the pilot) |
| 319.5638 | 01:26:09 | -1.75 | NO 0.35 x5 utsa-uab (01:25:20) |
| 316.7138 | 01:27:11 | -2.85 | NO 0.57 x5 lac-dal (01:26:37) |
| 319.7638 | 01:30:16 | +3.05 | YES 0.39 x5 lac-dal (01:31:16 logged): -1.95 cost, +5.00 pair credit |
| 321.6638 | 01:32:20 | +1.90 | YES 0.62 x5 utsa-uab (01:31:50 logged): -3.10 cost, +5.00 pair credit |

Net venue cash change: **+$0.3500** on $9.65 spent, fees $0, rebates $0,
rewards $0, residual inventory 0. Unchanged through 02:45Z. No other fills,
reward credits or settlements on the account in the window.

Findings
- The venue nets a YES+NO pair at trade time and pays the $1 immediately.
  A completed pair is realized cash when the second leg fills; it does not
  wait for the game. No POSITION_RESOLUTION row will follow for these.
- The sports bot's settlement meter waits for a resolution row, so it still
  shows settled 0 for these pairs. Its ACCOUNT_RISK lifetime figure (-39.5815)
  did not move either: the venue leaves realizedPnl blank on these trades.
- Bot FILL log times trail the venue cash change by up to ~1 minute for the
  third fill (cash moved by 01:30:16; FILL logged 01:31:16).
- `l5-pmus-live` preflight at 01:30:05 FAILED `no_unexpected_open_orders`
  (2 found): the lanes share one account and that lane saw the sports bot's
  resting orders as foreign. Passed again at 01:32:06.

Scale: +$0.35 from one 11-minute window with four fills is one observation,
not a rate.
