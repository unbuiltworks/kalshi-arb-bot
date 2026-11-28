# Production death drill — draft terms (live resting order; not authorized)

Prepared 23 Nov 2026. **This is a live, money-capable test and needs its own
exact authorization.** It is not part of either deployment request. It can
only run after both deployments and after the watchdog's two settings are
on.

## Purpose

Show in production that when the sports bot dies with an order resting, the
independent watchdog cancels that order by itself.

## Draft terms

| Item | Value |
|---|---|
| Market | one pre-game market, chosen on the day: quiet, inside the 25c to 75c band, priced by the international guard, one side at 35c or less |
| Order | one maker bid of 2 contracts on the cheap side |
| How one side only | account-risk check set to enforce for the drill; with $1.33 of capacity the dearer side does not fit and is refused |
| Capital committed | at most $0.70 |
| Maximum settlement loss | at most $0.70 (the bid fills and that side loses) |
| Trading window | 3 minutes |
| Death | the bot exits with no cleanup 60 seconds into the window and stays down until 60 seconds after the window ends |
| Pass | the watchdog cancels the order within 60 seconds of the exit, the venue shows it cancelled, and nothing else on the account is touched |
| Fail | the order still rests when the bot comes back; the bot, booting locked, cancels it itself; the watchdog change is not relied on |
| After | lock stays on; account-risk check back to observe |

If the bid fills before the exit, the drill is void, the two contracts are
held to settlement, and the loss is at most $0.70.

## Settings it would need (all temporary)

A three-minute submission window; the death-drill window; account risk
enforcing with the drawdown figure required; the pre-game slate so a
pre-game market can be selected; one permitted competition for the chosen
market.
