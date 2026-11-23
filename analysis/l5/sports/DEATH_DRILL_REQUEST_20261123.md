# Production death drill — exact terms for authorization

Prepared 23 Nov 2026, 12:40 UTC. **A live, money-capable test. Not
authorized until the owner approves these exact terms.** It replaces the
draft in `DEATH_DRILL_DRAFT_20261123.md`.

## Purpose

Show in production that when the sports bot dies with an order resting, the
watchdog deployed today cancels that order by itself.

## Prerequisite (a separate approval)

Sports bot on `release/pmus-sports-rn1-8eb920772`
(`8eb920772e9cfadbc58eaeb5899ebb973a7776d0`), deployed locked. It adds only
the market-and-side pin to the build running now (2 files, 135 lines; full
suite 5,300 passed, 24 failed, the same 24 as before).

## Terms

| Item | Value |
|---|---|
| Market | `atc-sea-mon-fio-2026-11-23-mon` (Monza to beat Fiorentina, Serie A, kick-off 19:45 UTC) |
| Side | YES only, by pin |
| Order | one maker bid, 2 contracts, at 25c or below (bid 0.25, ask 0.26 at 12:34 UTC; 1,061 resting at the best bid; 175 contracts traded in the market's life) |
| Capital committed while the bid rests | at most $0.50 |
| Maximum settlement loss | at most $0.50: the bid fills and Monza do not win |
| If the bid fills before the exit | drill void. The bot may rest a completing NO bid at 72c or below; if both fill, the pair is locked at a profit. Capital committed could then reach $1.94; the maximum loss stays $0.50 |
| Trading window | 3 minutes, starting at the first of 14:30, 16:30 or 18:30 UTC that is at least 40 minutes after approval |
| Death | the bot exits with no cleanup 60 seconds after the window opens and stays down until 60 seconds after the window closes |
| Pass | the watchdog cancels the bid within 90 seconds of the exit; the venue shows it cancelled; nothing else on the account is touched |
| Void | no bid is resting at the exit (the marginal test or a gate refused it, or it filled). Nothing further is attempted |
| Fail | the bid still rests when the bot returns; the bot boots locked and cancels it itself |
| After, in every case | locked; drill switch off; settings below restored |

## Settings changed for the drill, all restored afterwards

| Setting | For the drill | Afterwards |
|---|---|---|
| `PMUS_RN1_PIN` | `atc-sea-mon-fio-2026-11-23-mon:YES` | left (a pin only narrows) |
| `PMUS_TWO_SIDED_SLATE` | `both` | `live` |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | `nfl,nba,sea` | `nfl,nba` |
| `PMUS_RN1_RESIDUAL_CAP_USD` | `0.60` | `3` |
| `PMUS_RN1_TOTAL_UNMATCHED_USD` | `0.60` | `6` |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `window:<start>/<start+3min>` | `1` |
| `PMUS_TWO_SIDED_DEATH_DRILL` | `window:<start+1min>/<start+4min>` | `off` |

If this session stalls after the drill, what is left is safe: the window
has expired (locked), the drill window has expired, the pin narrows, and
the two caps are tighter than normal.

Serie A is added to the permitted list for the three-minute window only.
The $3 limit is unchanged; the $0.50 sits inside the $1.33 left.
