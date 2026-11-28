# Independent fair-value coverage, quota, freshness and matching — 28 Nov 2026

Production `8f7cb2c36` (deployment `26b47654`, booted 11:59:58Z) runs with `PMUS_TWO_SIDED_SUBMISSION_LOCK=1` (`why: locked`) and `PMUS_SHARP_COVERAGE_LOG=1`. All readings below are read-only. Nothing was placed.

## 1. Sportsbook provider (The Odds API), measured in production

| Item | Measured |
|---|---|
| Key configured | yes (`key_set: true`) |
| Sports offered by the provider | 78 |
| Tennis keys offered (ATP, WTA, Challenger, ITF, UTR) | **none** at 11:59Z (`tennis: []`) |
| Table tennis (Setka Cup, WTT, Czech Liga Pro) | **none** (`table_tennis: []`) |
| Configured sport keys offered | NFL, NCAAF, NBA, NCAAB, NHL |
| Configured sport keys missing | NCAAW basketball |
| Sports actually polled | NBA and NHL only (`sharp.sports`) |
| Bookmaker preferred | Pinnacle (region `eu`). The NBA rows came from `source: pinnacle` |
| Quota | 500-call plan: 238 used, 262 remaining at 12:00Z. Each odds call costs 1 (two calls took `used` from 236 to 238) |
| Poll interval | `PMUS_SHARP_POLL_S` = 10,800 s (3 h) |
| Odds-age limit | 900 s (`MAX_ODDS_AGE_S`) |
| Live (in-play) odds | **not used**. Candidates are pre-game only: start in now+5 min to now+6 h |

The plan price and terms could not be read: the provider's site does not resolve from the research sandbox. The quota above is from the provider's own headers.

### Freshness

| Time | NBA ORL–WAS odds age | Note |
|---|---|---|
| 12:00:02Z | 18.5 s (fresh fetch) | Pinnacle |
| 12:10:04Z | 620.8 s (cached) | still under 900 s |
| From about 12:15Z | over 900 s, so **no fair** | until the next fetch, 3 h later |

With a 3 h poll and a 15 min age limit, a sportsbook fair is usable about **8% of the time** (15 of every 180 minutes). Keeping NBA and NHL fresh by polling (one call per sport every 15 min for about 8 h of game time) would need about 64 calls a day. That is about 1,900 a month against 262 remaining. **Quota, not code, is the binding constraint.** The dev branch adds `PMUS_SHARP_REFRESH=on_demand` with a daily call cap (default 8), so the quota is spent only when a candidate needs fresh odds. It is off by default and not deployed.

### Matching

| Sport | Shadow candidates | Matched | Cause |
|---|---|---|---|
| NBA | 2 | **2** | Polymarket US uses full names ("Orlando Magic"), the same as the feed |
| NHL | 8 | **0** | Polymarket US uses **nicknames only** ("Lightning", "Canadiens", "Maple Leafs"). The feed uses full names, and matching was exact |

Fix on dev: `PMUS_SHARP_NAME_MATCH=words`, default off.
- Matches whole words at the start or end of a name ("Lightning" ~ "Tampa Bay Lightning", "Air Force" ~ "Air Force Falcons"), with accents folded.
- Both teams must pair one-to-one and the start time must agree within 30 min. A name that could fit either feed team is refused.
- 7 tests.

## 2. International Polymarket book (the guard's fair source)

- This is a public, keyless, unmetered order book. It is the source behind the 21 Nov tape results.
- The production guard's WebSocket is not connected (`intl_guard.ws.connected: false`). In the last campaign it matched 0 markets (`unmatched` 193).
- Slate check (12:06Z), for US markets starting between now−3 h and now+18 h with the same slug on the international venue:

| Listed internationally | Not listed internationally |
|---|---|
| CBB 60/63, CFB 58/62, NHL 7/13, NBA 5/5, AHL 1/13, CS2 4/13, Dota2 2/4 | **Setka Cup (UA/MD/CZ/WoUA), WTT, Czech Liga Pro, UTR, ATP, ATP doubles, WTA, ITF**: 0 of about 450 |

## 3. Reward programmes on today's slate (`/v1/incentives?symbols=…`, 12:25Z)

This is a per-market symbol query. The paged list repeats the same 100 rows, so it is not a census.

| Sport | Markets | With programme | Active pool | Per market | Independent fair available |
|---|---|---|---|---|---|
| NHL | 13 | 13 | $4,979 | $383 (live $350, target size 8,000; day-of $33, target size 12,000) | intl 7/13; Pinnacle once names match |
| Setka Cup UA | 169 | 168 | $3,696 | $22 | **none** |
| UTR | 56 | 56 | $3,360 | $60 (live $50, target size 500) | **none** |
| WTT | 74 | 74 | $1,628 | $22 | **none** |
| ATP | 7 | 7 | $1,568 | $224 | **none** (no tennis keys, not internationally listed under the same slug) |
| CFB | 62 | 62 | $1,188 | $19 (live only, target size 86,000) | intl 58/62; NCAAF key |
| NBA | 5 | 5 | $150 | $30 (live only, target size 75,000) | intl 5/5; Pinnacle 2/2 |
| CBB, WCBB, AHL | 147 | 0 | $0 | — | CBB intl 60/63 |
| **ETH, SOL, XRP, BTC 5m and 15m up/down** | 68 windows | **0** | $0 | — | (for #250) |

## 4. Conclusions

1. **Independent fair coverage on the markets we traded is zero.** Setka, UTR, ATP doubles and WTT, where all 24 campaign fills came from, have no sportsbook key and no international listing. That is why trading-first would place nothing there, and it should not.
2. **The overlap of fair coverage and reward programmes is NHL, NBA and CFB in-play.** Those programmes have large target sizes (8,000–86,000 contracts), so a 2-contract quote earns a negligible share. There, rewards are a small supplement and trading EV must carry the strategy. That is the intended trading-first ordering.
3. **The international book is the practical fair source:** free, in-play, and it leads Polymarket US by 3–30 s (21 Nov). The sportsbook is useful pre-game only, and only with a larger quota.
4. **No reward-eligible ETH, SOL or XRP up/down markets exist right now** (0 of 68 windows). Crypto stays with #250, unchanged and disarmed.

Files:
- `analysis/l5/sports/fair_coverage_20261128/programs_by_symbol_20261128.json`
- `analysis/l5/sports/fair_coverage_20261128/slate_intl_20261128.json`
