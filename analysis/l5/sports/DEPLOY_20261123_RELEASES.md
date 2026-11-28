# Deployments of 23 Nov 2026 — watchdog and sports bot (locked)

Both approved separately and directly by the owner (see section 11 of
`STANDING_AUTHORIZATION_20261122.md`). No order was placed, nothing was
armed, no competition was added and no limit was changed.

## Watchdog — `l5-halt-watchdog`

| Step | Time (UTC) | Result |
|---|---|---|
| Source connected to `release/l5-halt-watchdog-ce26e2a9e` at `ce26e2a9e` | 12:12 | deployment `ab16d5dd`, SUCCESS 12:13:22 |
| Sweeps compared with the previous build's last four | 12:13 to 12:15 | same 19 fields, same values: 20 s cadence, age limit 300 s, cancel on, zero open orders, three registries OK, no halt |
| `L5_WATCHDOG_REMEMBER_CLAIMS_S=900`, `L5_WATCHDOG_DEAD_AFTER_S=30` added | 12:16 | deployment `51102e25`, SUCCESS 12:17:21 |
| 26 consecutive sweeps read | 12:17 to 12:25 | identical to before; no cancel, no halt, no error |

Not shown by this: the new behaviour itself. Nothing in the watchdog's log
reports the two settings, and it only acts when a registry is unreachable.
The sports bot's own restart left its registry down for about 11 seconds,
which fell between two sweeps, so the watchdog never saw it unreachable.
The production proof remains the death drill, which is not authorised.

## Sports bot — `pmus-spring-l5-probe`

| Step | Time (UTC) | Result |
|---|---|---|
| Source connected to `release/pmus-sports-rn1-02e5141b1` at `02e5141b1`; no variable changed | 12:16 | deployment `0fa88d3e`, SUCCESS 12:17:25 |
| Old process shut down | 12:17:19 | cancelled its own orders on the way down; none were open |
| New process boot | 12:17:30 | lock on (new exposure locked, completions locked, reason "locked"); live slate; order size 2; marginal test on; fair value must hold 10 s within 1c; account risk on observe; reader started |
| First audit | 12:19:34 to 12:21:07 | inventory reconciled, no mismatch; fills confirmed; cash $319.8038 as before |
| Order service during that audit | | longest gap 9.2 s. On the previous build the same audit left orders unserved for about 87 s |
| State at 12:25 | | locked, no safety latch, zero orders, zero fills, no errors, no active market |

Checked after, not before: I connected the new source without first
re-reading the lock. The process being replaced was the one verified locked
at 23:45 UTC on 22 Nov and not restarted since, it shut down with nothing
open, and the new process booted locked.

Not yet confirmed in production:

- **The drawdown reading.** The account-risk reader started, but no
  account-risk line has been logged because the check is only evaluated when
  a market is active and none was (no permitted game was in play at noon
  UTC).
- **Market steps during an audit.** With no active market, the 9.2-second
  gap is the evidence that orders were served; no market step was logged.
- **The reward check** was deferred by the request budget at the start of
  the audit, as it was on the previous build.

Rollback, if wanted: sports to `release/pmus-sports-rn1-158685d3a` at
`158685d3a`; watchdog to `fix/l5-watchdog-lane-clock-skew-20261117` at
`faebeca6f`, or remove its two settings.
