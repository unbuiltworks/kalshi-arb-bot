# First 12-hour rolling campaign — draft request

24 Nov 2026. **Draft. Not approved. Nothing is armed.** It becomes a
request once its preconditions are met; figures marked TBC are filled from
evidence, not assumed.

## Preconditions (state at 11:35 UTC, 24 Nov)

| # | Precondition | State |
|---|---|---|
| 1 | Account limit and audit protection shown under live traffic | done, 01:20–02:20 UTC window |
| 2 | Watchdog `f6a5900eb` deployed | done; marker mechanism still off (operator's two secrets not set) |
| 3 | Combined sports release `12d5d3daf` deployed locked, 12-hour maximum window | done |
| 4 | Drill 1 (cancel after process death) | passed 05:01 UTC; halt reset 11:28 UTC |
| 5 | Interim variable drill | deferred by the owner |
| 6 | Fast emergency stop: two secrets set, wrong-signature check, signed-marker drill, halt reset | **open: waits on the operator's two secrets** |
| 7 | Exact campaign authorization (window, terms below) | open |

## How it runs

One 12-hour submission window set on the sports bot (one settings change,
one restart before the window). It relocks by itself at the end. Runtime
campaign files stay off: they would need a third secret and add nothing
to this first run. The halt marker, the watchdog's own rules, the account
limit, the loss stop, the daily halt, the stale-audit block and the safety
latch all act without a session.

## Proposed campaign file

| Field | Value |
|---|---|
| Window | 12 hours, start set in the authorization (chosen so both a soccer slate and tennis fall inside it) |
| Competitions | ATP, Serie A, Premier League, La Liga, NBA, NFL, NHL (owner, 24 Nov). Eligible is not the same as quoted: every market must still pass the price, economic, inventory and risk gates |
| Markets at once | up to 4, subject to the room left under the account limit |
| Order size | 1 contract |
| Slate | pre-game and in-play |
| Campaign spend / unpaired | $16 / $6 |
| Per-market spend / unpaired | $8 / $3 |

Fixed outside the file: $3.00 account limit (enforced), $4 campaign loss
stop, $5 daily halt, maker-only, 0.97 pair cap, 10-contract depth,
international price required, 75-minute stale-audit block.

**The binding limit is the account figure.** With $1.66 of room, about
three markets at one contract can be quoted at once, and a realized loss
of $1.66 ends new exposure for the rest of the campaign. A different
ceiling is a separate decision for the owner, to be made on tonight's and
this campaign's results.

## Quoting behaviour proposed for this campaign (TBC from the comparisons)

| Setting | Proposed | Decided by |
|---|---|---|
| Hold a resting bid through a short price-feed gap | on, 20 s | side-by-side dry runs, 24 Nov |
| Keep a bid left one tick deeper than its target | on, 1 tick | same |
| Quote depth | 1 or 2 ticks behind the best price | same, plus the jump recording |
| Market selection | by expected economics, not equal shares: the ranking that is already built orders candidates by the expected trading result of a first bid. Its reward term is added per competition only where the venue has actually PAID rewards on our quoting | reward rows for 22 and 23 Nov |

## The report (at 3, 6, 9 and 12 hours and at close), in the owner's order

1. Confirmed realized net profit (venue cash and settlement records).
2. Paid liquidity rewards and rebates; pending and skipped shown apart
   and not counted.
3. Stand-alone inventory profitability: each fill by role, held to
   settlement, apart from pairing.
4. Capital utilization and turnover: dollars committed against the room
   available, by hour; capital released on settlement.
5. Reward-qualifying resting time against total resting time.
6. Adverse selection and execution cost: marks at 5 s to 5 min by role,
   order age at fill, cancels per order.

Each by competition, so soccer and tennis can be compared per dollar
committed.

## What it must answer

1. **Soccer against tennis:** trading result plus paid rewards per dollar
   committed per hour, by competition.
2. **Accumulation against pairing:** each fill by role, with its mark and
   its stand-alone settlement result (report already built).
3. **Persistence:** resting time, reward-qualifying time, cancels per
   order, order age at fill.
4. **Reliability over 12 hours:** every hourly audit completed; account
   limit behaviour; capital released on settlement; no manual action
   needed.

## Stops

In the bot: expiry, account limit, loss stop, daily halt, stale audit,
safety latch, unreadable or revoked campaign file. Outside it: the halt
marker (operator, one commit), the watchdog's own rules.

Economic stop by review at the 3-hourly reports (the operator removes the
campaign file): two unpaired legs marked down more than 10c each; or a
competition with six or more fills, mean 30-second mark worse than −2c and
fewer than half paired.

## Not part of this request

Any increase of a ceiling or limit, taker orders, unpriced markets,
cricket, table tennis, Dota 2, a halt reset, automatic re-issue of the
campaign file.
