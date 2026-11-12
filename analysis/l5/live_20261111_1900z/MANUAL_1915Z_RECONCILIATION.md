# 2026-11-11: automated and manual results, kept apart

Three figures. None of them replaces another, and no ledger row or limit was
changed to produce this note.

## 1. L5 automated test, `cpc-btc-updown-15m-2026-11-11-1900z`

**-0.1410, venue-confirmed.** UP 0.30 @ 0.47 (`DNK8WP3G14F0`), DOWN won.

## 2. Owner's manual trade, `cpc-btc-updown-15m-2026-11-11-1915z`

Confirmed by the owner as manual. `l5-pmus-live` submitted 0 orders in this
market and was disarmed from 19:12:16Z.

| | |
|---|---|
| Position at settlement | DOWN 5.0000 (venue position row, 19:29:10Z) |
| Outcome | UP won; redeemed 0 |
| Ledger entry | cost 5.0000, P&L **-5.0000**: the $1.00-per-contract upper bound, booked because the cost was unknown to the service |
| Cost estimated from account cash | **2.41 to 2.44**, about 0.48-0.49 per contract |
| Estimated actual result | **about -2.42** |

How the estimate is made, from the service's own heartbeats (available cash):

| UTC | cash | change |
|---|---|---|
| 19:15:54 | 344.13605 | |
| 19:16:56 | 343.96605 | -0.170 |
| 19:17:58 | 343.51605 | -0.450 |
| 19:19:02 | 343.53505 | +0.019 |
| 19:21:10 | 343.35705 | -0.178 |
| 19:22:11 | 343.08505 | -0.272 |
| 19:23:12 | 342.41105 | -0.674 |
| 19:24:15 | 343.83505 | +1.424 |
| 19:25:17 | 341.73505 | -2.100 |
| 19:31:30 | 341.73505 | unchanged through settlement |

Net -2.401 over the span. The only other position in it was 0.10 DOWN in the
5-minute 19:15Z market, which redeemed +0.10 and cost an estimated 0.06-0.09.
So the 15m position's net cost is 2.501 minus that: 2.41 to 2.44.

**This is an estimate, not the fill record.** Cash moves with resting-order
reservations and with any trade opened and closed inside the span, and the
service holds no fill for an order it did not place. The exact cost is the
venue's own trade list for this market (trade id, quantity, price).

## 3. Account-wide, 18:55Z to 19:36Z

| | |
|---|---|
| Cash 18:55:18Z | 344.20905 |
| Cash 19:36Z | 341.73505 |
| Change | **-2.474** |
| of which L5 automated | -0.141 |
| of which manual, by difference | -2.333 |

Earlier the same afternoon (17:16Z to 18:55Z) cash rose from 340.93215 to
344.20905, +3.277, with no L5 order. The ledger saw only four 5-minute
positions of 0.1-0.3 shares in that time, so that rise is not explained by
positions the service observed; it may include deposits, other markets, or
trades opened and closed between the service's five-minute reconciliations.

## What stays as it is

- The -5.0000 ledger row. It is a conservative placeholder; it is not erased.
- The $5.00 daily halt (tripped on the account-wide figure, -5.0138) and the
  $18.6325 lifetime limit (account-wide figure -21.0406).
- Both L5 locks. The service is disarmed.

## What a correction needs

1. The venue's trade list for `cpc-btc-updown-15m-2026-11-11-1915z`.
2. From it, a restatement entry of the same kind prepared for 04:15Z
   (original row kept, measured cost, per-fill trade ids, idempotent), with
   the position attributed MANUAL rather than L5.
3. The owner's explicit approval before it is applied.

With a cost of about 2.42 the account-wide figures would read about -2.43 for
the UTC day and about -18.46 all-time: under the daily halt, and about $0.17
inside the lifetime limit.
