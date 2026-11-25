# Spreads, totals, props and combos on PMUS — first scan

25 Nov 2026, 02:55–03:40 UTC. Read-only, public gateway only: no signed
request, no order, no code change. One snapshot, late Tuesday night US
time. Every reward figure is a model estimate, not a payment. Scripts and
raw samples: `scan_20261125/` (the two largest raw files, 130 MB, were not
committed).

## Decision summary

| Rank | Type | Verdict for small resting bids |
|---|---|---|
| 1 | Moneyline | Only type the bot handles end to end today. |
| 2 | Soccer three-way | Handled for mapped leagues; deep books, our share is tiny. |
| 3 | Full-game spread, main line (NBA, NFL, CFB, NHL) | Best new candidate: ordinary order book, by far the most traded new type, large live pools. Needs discovery and a view on fair price. |
| 4 | Full-game total (NBA, NFL, NHL) | Same structure, less traded. |
| 5 | Period spreads/totals, team totals | Pool shared across 40–90 markets a game; most touches under one contract; settle within minutes of a period ending. |
| 6 | Player props | Thinnest books; the large modelled share is an artefact of near-empty books; void rules settle at "last fair market price". |
| 7–8 | Period/set winners, other game props | Pools of $1–4; mostly no volume. |
| 9–10 | Combos (same-game and multi-game) | Not a resting-book product: quote-request flow with signed calls. Outside the existing controller. |

## Three findings that change the picture

1. **Reward pools are shared, not per market.** The venue's rewards page:
   the pool is "shared across the program's markets — never summed per
   market". Our reward model treats a pool as per market. That is right
   for moneylines (one market an event) and overstates every ladder type
   by the number of markets sharing the pool (34–325 a game here). Read
   that way, every new type models at $0.01–0.13 an hour per dollar, the
   same order as moneylines ($0.02). Whether a pool is per game or across
   a whole programme could not be determined from public data.
2. **The bot cannot see any of these markets.** Its discovery reads the
   event list, which carries only each event's primary market: 70 of 2,326
   live markets, 296 of 1,115 pre-game. The full set comes from one call
   per event (`/v1/events/slug/{slug}`).
3. **Cricket exclusion gap.** Five of the six cricket competitions on the
   venue were not caught by the bot's name rule. Fixed in release
   `bc3a5b6b9` (venue's own label now decides).

## Universe (live or starting within 30 h)

| Type | Open | Live | With a reward programme | Main leagues |
|---|---:|---:|---:|---|
| Moneyline | 1,193 | 65 | 881 | table tennis, UTR, ITF, college basketball |
| Three-way | 189 | 9 | 57 | J-League, UCL |
| Spread, full game | 2,623 | 613 | 648 | college basketball 1,802 (no programme), NBA 367, NHL 90, CFB 82, NFL 41 |
| Spread, period | 499 | 131 | 371 | NBA, NHL, NFL |
| Game total, full | 3,362 | 599 | 1,303 | college basketball, table tennis, NBA, NHL |
| Game total, period | 613 | 135 | 485 | NHL, NBA |
| Team total | 940 | 202 | 274 | college basketball, NHL, NBA |
| Player prop | 3,087 | 412 | 2,885 | NBA 1,453, NHL 1,167, NFL 273 |
| Period/set winner | 1,563 | 34 | 1,280 | table tennis |
| Other game prop | 1,437 | 100 | 501 | college basketball, NBA, UCL |

Cricket: 16 markets, all moneylines, excluded throughout.

## Order compatibility

- Verified: every non-combo type is an ordinary order book with the same
  public shape as a moneyline (770 books sampled, no errors); tick $0.01
  except 155 markets at $0.005 or finer; minimum quantity 0.01.
- 370 NBA player props list the No side as not tradable.
- Not determinable without a signed request: whether our account's orders
  are accepted on these slugs.
- Combos: a separate instrument created by a signed call, priced by
  request-for-quote with a last look; combo makers pay a fee and get no
  rebate. No liquidity programme found for them.

## Programmes (examples)

NBA spreads live $450 (target 11,250); NFL spreads live $6,300; NBA totals
live $600; NFL totals live $4,450; NBA player props live $700 (target 450)
across about 1,950 markets; NFL player props live $3,700; table-tennis
totals live $8. Day-of pools are $3–27. No programme on college
basketball spreads, totals or props (60% of all spread and total markets).

## Books and activity (paying markets priced 0.25–0.75)

| Type | Median contracts traded | Median spread, ticks | Books with a touch under 1 contract |
|---|---:|---:|---|
| Moneyline | 137 | 2 | 26 of 70 |
| Spread, full | 14,510 | 2 | 19 of 54 |
| Game total, full | 0.6 | 2 | 35 of 65 |
| Spread, period | 7 | 8.5 | 25 of 67 |
| Team total, full | 29 | 3 | 24 of 53 |
| Player prop | 403 | 6 | 34 of 64 |
| Period/set winner | 0.1 | 2 | 49 of 70 |

## Settlement terms that differ from moneylines

- No pushes: every spread and total line is a half point.
- Voids settle at the "last fair market price", not a refund: postponed
  beyond 48 hours; NBA player who never takes the court; NHL player with
  no ice time; NFL player with no snap.
- Period markets exclude overtime; full-game spreads and totals include it.
- Table tennis walkover or unresolvable retirement settles at $0.50.

## Would the existing controller trade them without code changes? No.

| Gate | Effect |
|---|---|
| Slug prefixes | already admitted (`asc-`, `tsc-`, `astatc-`) |
| Discovery | never lists them (primary market only) |
| Price band, spread and thin-touch rules | reject most ladder lines and dust-touch books, as intended |
| Late-game rules | would not protect: no rule knows a quarter or period market's own ending |
| Reward ranking | would overstate them by the pool-sharing factor |
| Order placement | nothing keyed on slug shape; venue acceptance unverified |

## Correlated exposure

One NBA game carried 703 open markets. A scoring run moves the moneyline,
all 34 spreads and the margin markets together; totals, team totals and
player lines are nested ladders. The code's only event-level control is
one market per game at selection; no dollar cap sums exposure across the
markets of one event. Quoting several lines of a game would need that
rule relaxed and an event-level cap added.

## What a bounded validation would need (not started)

Full-game main-line spreads and totals in NBA, NFL and NHL are the only
candidates worth a bounded test. Before one: (1) per-event discovery for
those two types; (2) the pool-sharing divisor in the reward model, settled
against credited reward rows; (3) one market per game kept, so no
event-level cap is needed yet; (4) a late rule for these markets (the
game's own late rule applies to full-game lines); (5) one supervised
one-contract order to prove the venue accepts the slug. Player props,
period markets and combos are not proposed.
