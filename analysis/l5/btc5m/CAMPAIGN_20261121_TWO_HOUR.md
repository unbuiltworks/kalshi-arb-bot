# Two-hour measurement campaign, 2026-11-21 — armed 16:17Z, DISARMED EARLY 16:48Z

Label: LIVE PROBE, SETTLED. Lane 2fe56d9bf, account service 62b54e14a. Listed 24 markets 1630z-1825z; two traded.

| Window | Orders | Fills | UP | DOWN | Cost | Pair cost | Won | Settled net |
|---|---|---|---|---|---|---|---|---|
| 1630z | 47 (13 filled, 16 cancelled, 18 venue-rejected) | 13 | 0.50 / 0.2159 | 0.45 / 0.2537 | 0.4696 | 0.996 | DOWN | -0.0196 |
| 1635z | sat out (previous window unsettled at its start) | | | | | | | |
| 1640z | 14 (8 filled, 3 cancelled, 3 venue-rejected) | 8 | 0.31 / 0.1918 | 0.31 / 0.1471 | 0.3389 | 1.093 | UP | -0.0289 |
| 1645z | sat out (previous window unsettled at its start) | | | | | | | |

Campaign: **-$0.0485**. Lane, account ledger and cash agree on both windows (differences 0.0000):
ledger 1630z -0.05 -> -0.0196 (2 min after settlement), 1640z -0.08 -> -0.0289 (2 min); cash 321.4304 -> 321.3819.
Day (four live windows): -$0.0698. Fees 0. No uncertain orders, no latch, no watchdog cancel or read error.

## Why it was disarmed
The flat submission total (`L5_LIVE_AUTH_MAX_SUBMISSIONS`, 60 per campaign approval) was reached at 16:41:09Z
(47 + 13). From then every non-reducing order was refused; orders that reduce unmatched inventory are exempt from the
flat total by design (one such DOWN bid was accepted at 16:41:32 and filled). The lane could complete but not open or
requote for the remaining 21 windows. Disarm applied 16:48Z during a sat-out window; verified: MONITOR,
can_submit false, 0 open orders, registry readable through the redeploy.

## What ran for the first time in production, and held
- Campaign document covering listed windows; no window outside the list was armed.
- No-grace reconciliation rule: both earlier markets checked and passed at arming.
- Sit-out rule: 1635z and 1645z refused once (CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED) then sat out; never joined part-way.
- Sibling-ledger correction of both windows in about two minutes each.

## 1640z: why the pair cost 1.09
UP bid 0.41 -> 0.69 -> 0.42 in ~80 s. DOWN 0.08 @0.58 filled at 16:40:07.3 (0.4 s after it was accepted). At 16:40:12 the
lane was refused for 22 s (portfolio value unavailable while the account service adopted that position), cancelled its
resting UP 0.41 bid and held the DOWN leg unquoted while UP went to 0.61. It then bought UP at 0.62, 0.66, 0.61, 0.59
and DOWN at 0.33, 0.40, and a last completing DOWN at 0.57.

## Follow-up prepared (NOT deployed)
Account service `release/l5-pmus-live-sibling-value-20261121` at `7c3d69ba055c986ec8cf7d1583eb290579644390`: keeps the conservative
portfolio value published while the only uncertainty is another strategy's just-adopted position. Off by default
(`L5_PUBLISH_VALUE_DURING_SIBLING_ADOPTION=OTHER_STRATEGY_POSITION_ONLY`). Patch:
`patches/pmus_live_value_during_sibling_adoption.patch`.
