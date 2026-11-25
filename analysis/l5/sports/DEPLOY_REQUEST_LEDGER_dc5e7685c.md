# Ledger release candidate `dc5e7685c` — bot-only and total-account risk figures (not deployed)

25 Nov 2026. Service `l5-pmus-live` (the account ledger every lane reads).

| | |
|---|---|
| Release SHA | `dc5e7685cc2e69456b2cc680fb18cc3887479677` |
| Branch | `release/l5-pmus-live-bot-total-split-20261125` |
| Running now | `f5a5434d6c3530e501c76879345b5199813efa9b` (rollback target) |
| Change | 4 code commits on top of the running build; 2,010 lines added, 1,215 of them tests |
| Tests | 135 new cases pass. Full suite: the failing test ids are identical before and after (98 = 98, all pre-existing). |
| Review | first commit read line by line by me; the three later commits checked by their tests and reports, not line by line |

## What it does

Off unless `L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD` is set. Deploying it
changes no number.

Switched on, the ledger keeps two figures and enforces both:

- **Total account** = anchor minus worst-case account value: everything,
  manual included, against the new total limit.
- **Bot only** = total minus the measured net cash of the owner's manual
  orders, against the existing bot limit (`L5_PORTFOLIO_MAX_DRAWDOWN_USD`).

A bot is refused when either limit is reached. Existing readers (the
sports bot, the crypto lanes) need no change: the published room is the
smaller of the two.

Manual orders are never taken as a dollar amount on trust. The owner lists
order ids in `L5_MANUAL_ATTESTED_ORDERS`; the ledger reads each from the
venue and counts it only if the venue's own row marks it manual. Buys
count as cash out, closing sells as cash in, so a manual gain raises the
bot figure instead of hiding bot losses, and a manual loss does not
count against the bots. Anything it cannot measure is charged against the
bots at its worst case.

## Settings to switch it on (each needs approval)

| Variable | Value | Note |
|---|---|---|
| `L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD` | owner's choice, at least the bot limit | the real account-wide stop once the split is on |
| `L5_MANUAL_ATTESTED_ORDERS` | the buy and the sell order id of the NHL trade | both, see risk 1 |
| `L5_PORTFOLIO_ANCHOR_USD` | +5.00 if the 02:20 UTC step was a promotion credit | existing deposit path |

Expected reading after switching on with both NHL orders attested and the
anchor raised by $5: bot figure about $3.2 (the 2.61 before the manual
trade plus the open Carjem–Caltes cost and small moves), not −9.03.
I will read the actual figure and report it before any window opens.

## Known limits

1. **An unattested manual sale can still mask.** If only the buy is
   listed and the position was sold by hand, the ledger may treat it as
   still held and later as settled at the game's result. Listing both
   orders is exact. Rule for use: every manual order id is listed, buys
   and sells.
2. **A manual sell row has never been seen by this code.** If the venue
   spells it differently from what is expected, the order is treated as
   unmeasured and the bots are charged its worst case (up to about $35
   for this trade): safe, but it would block the bots until fixed.
3. A settlement at $0.50 (void-style) is never retired by the ledger, so
   such a manual position reads as held indefinitely.
4. Between the venue settling a won manual position and the ledger
   retiring it, the gain is briefly not applied.
5. The ledger's separate equity-baseline block and its own lanes' exposure
   controls are not split; the sports bot does not read them.
6. If the ledger's tick stalls for more than 180 s, manual losses stop
   being credited (gains still apply): bots may be refused until it
   recovers.

## Crypto lanes

They read the same block. With the split off nothing changes for them.
With it on, their room is the smaller of the two rooms, never larger than
what the total limit allows; the crypto lanes remain disarmed.

## Approval needed

Deploying the build (changes nothing by itself), and separately each
setting above. The ledger restarts on deploy and on each setting.
