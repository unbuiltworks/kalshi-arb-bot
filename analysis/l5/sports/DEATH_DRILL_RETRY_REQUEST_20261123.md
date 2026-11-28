# Production death drill — first attempt void; retry terms for authorization

23 Nov 2026, 15:15 UTC. **Not authorized.** The bot is locked and stays
locked until the owner approves a new drill.

## First attempt, 14:30 to 14:33 UTC — void

- No order was placed: the pinned market
  (`atc-sea-mon-fio-2026-11-23-mon`, bid 0.25) was never selected and the
  bot logged no reason. Nothing was at risk, nothing was cancelled, no halt
  latched.
- The death part ran as designed: exit with no cleanup at 14:31:03.8, the
  same deployment restarted within a second, a silent hold to 14:34:00, a
  normal boot, locked.
- The watchdog reported the bot's registry unreachable on nine consecutive
  sweeps (14:31:07 to 14:33:47) and, with nothing resting, cancelled
  nothing and latched no halt.
- Not shown: the watchdog cancelling a resting order.

Settings were restored at 14:38 and verified at 15:04 UTC: locked (reason
"locked"), drill switch off, per-market and campaign unpaired caps back to
$3 and $6, permitted competitions NBA and NFL, zero orders, zero fills
since 13:38, inventory reconciled, watchdog clean. The pin is still set to
the Monza market; it only narrows and is inert under the lock.

## Why the market was not selected — not established

- With the same drill settings on a test copy of the code, the market is
  selected. Production differs in a setting.
- Every filter between the listing and the price guard was checked against
  the venue's own listing: the market was in the pre-game listing, had a
  two-sided quote, was not excluded or late, and Serie A was permitted.
  What is left is the price band: production has its own
  `PMUS_TWO_SIDED_BAND_LO` and `PMUS_TWO_SIDED_BAND_HI` (their names are
  visible), and the bid of 0.25 sat exactly on the default lower edge.
- The owner authorised reading those two values. I did not: the only tool
  available returns every variable in plain text, credentials included, and
  a narrower query was refused.
- So this stays a strong suspicion, not a finding.

## Logging build (not deployed)

`release/pmus-sports-rn1-62dca41cd`
(`62dca41cd7167fab117753a2f010fddffd95d55c`, code `c337c3aef`). Logging
only on top of the build running now: a pinned market that is not selected
logs exactly why, and the boot line prints the band and the other selection
settings. No decision changes. Full suite 5,305 passed, 24 failed, the same
24 as before.

Void: `release/pmus-sports-rn1-a14c438b6` was pushed in error. It carries
that label but not the change, and the remote refused its deletion. It must
not be deployed.

## Retry terms

| Item | Value |
|---|---|
| Build | `62dca41cd7167fab117753a2f010fddffd95d55c`, deployed locked first |
| Market | `atc-sea-mon-fio-2026-11-23-fio` (Fiorentina to win at Monza, Serie A, kick-off 19:45 UTC). Bid 0.49, ask 0.50 at 15:09 UTC; international 0.50 / 0.51; mid-band |
| Side | YES only, by pin |
| Order | one maker bid, **1 contract**, at 49c or below (the bot would bid 0.48) |
| Capital committed while the bid rests | at most $0.49 |
| Maximum settlement loss | at most $0.49 |
| If the bid fills before the exit | drill void; the bot may rest a completing NO bid at 48c or below; committed capital could reach $0.97; maximum loss stays $0.49 |
| Locked pre-flight | before the window, with the lock on: pin and slate set, then confirm from the log that the market IS selected and the bid would pass, or read the reason it is not |
| Window | 3 minutes at 16:30 UTC, or 18:30 UTC if approval or the pre-flight comes too late for 16:30 |
| Death | exit with no cleanup 60 s in; down until 60 s after the window |
| Pass | the watchdog cancels the bid within 90 s of the exit and the venue shows it cancelled |
| Known side effect | the watchdog latches the shared halt when it cancels; every lane stays halted until a separate owner-approved reset |
| No retry inside this authorization | if the pre-flight shows the market is not selected, nothing is armed |

Settings for the drill, restored afterwards: `PMUS_RN1_PIN` to the market
above, `PMUS_RN1_QTY` 1 (back to 2), `PMUS_TWO_SIDED_SLATE` both (back to
live), permitted competitions plus Serie A (back to NBA and NFL), the two
unpaired caps 0.60 (back to 3 and 6), the 3-minute submission window (back
to 1), the death-drill window (back to off).

## Second attempt, 16:30 to 16:33 UTC — the watchdog FAILED

Build `62dca41cd`, watchdog `ce26e2a9e` with both settings on. Terms as
authorised: one contract, YES at 0.48, `atc-sea-mon-fio-2026-11-23-fio`.

| Time (UTC) | Event |
|---|---|
| 16:30:02 | window opened |
| 16:30:04 | first bid placed (YES 0.48 x1); pulled by the bot about 11 s later when the international price dropped out |
| 16:30:52.6 | second bid placed (YES 0.48 x1) |
| 16:31:03.4 | bot exited with no cleanup; the bid was resting |
| 16:31:10 to 16:33:51 | nine watchdog sweeps: bot's registry unreachable, one open order seen every time, bot counted as gone from 16:31:51; nothing cancelled, no halt |
| 16:34:01 | bot booted, locked |
| 16:34:02.4 | bot cancelled the bid itself, 179 s after the exit |

No fill. No money lost. No halt latched. Inventory reconciled at 16:37 with
zero positions and cash unchanged. Settings restored at 16:36 and seen in
the 16:37:46 boot: locked, size 2, caps $3 and $6, live slate, NBA and NFL.

**Cause.** The watchdog reads each bot's list of its own orders once per
20-second sweep. Its last read before the death was at 16:30:50.9, 1.7
seconds before the resting bid was placed. What it remembered was the
earlier bid, already gone. The resting bid was never in its record, so
neither the gone-executor rule nor the age rule could select it as the
bot's. In play, where orders are replaced about once a minute, this would
happen often.

**Fix (not deployed).** The watchdog re-reads every bot's order list each
2 seconds between sweeps. The read is internal: no venue request, no
cancel, no halt. Release candidate `release/l5-halt-watchdog-8c9bee06f`
(`8c9bee06fe0faf7d0ed22aec2453acc1a2cad6be`, code `36238dd10`). The
production sequence is now a test, with and without the refresh; the
watchdog's test files pass (608). An order placed less than about two
seconds before a death can still be unknown to the watchdog; the bot's own
restart and the halt cover that, as before.

**Third attempt, for authorization.** Same terms as the second: one maker
bid of 1 contract, YES only, at 49c or below on
`atc-sea-mon-fio-2026-11-23-fio`; maximum settlement loss $0.49; 3-minute
window at 17:30 UTC, or 18:30 UTC if 17:30 cannot be met; exit with no
cleanup 60 s in; down 3 minutes; the shared halt latches if the watchdog
acts; settings restored after. Sports build `62dca41cd` (running);
watchdog `8c9bee06f` deployed first.
