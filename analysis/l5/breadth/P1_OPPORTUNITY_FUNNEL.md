# P1 — OPPORTUNITY FUNNEL, READ-ONLY

Capture `2026-09-13T17:41:25Z` · 3,763 events · 37,116 open markets · 15 categories
Source: `gamma-api.polymarket.com/events` (public, keyless). Generator:
`analysis/l5/breadth/scan_universe.py`. Regressions: `tests/test_breadth_universe_scan.py` (20).

**Nothing here is a strategy proposal, and no category is being recommended for the
P4 breadth ramp.** P4 is gated on another clean settled cycle, and the metric the
owner named as most important — expected realized economics — is *not computable
from this data*. See "What this cannot answer".

---

## 0. Read this before the tables: the first run was wrong four ways

The first version of this scan produced a complete, plausible-looking ranking
(`basketball 0.7955 | politics 0.7775 | crypto 0.755 | ...`). It was not reported,
because every one of these defects was found before interpretation. Recording them
because each would have pointed breadth research at the wrong venue, and none of
them announced itself as an error:

1. **`startDate` is the LISTING date, not the trading-window start.** It equals the
   event's deploy timestamp. `endDate - startDate` therefore measured how long a
   market had been *listed*: basketball scored a 345-day median "duration", and
   **every short-duration bucket was 0.0 across all 15 categories**. That 0.0 was
   the symptom that stopped the first report.

2. **`endDate` is a SCHEDULED end, not an actual one.** 5,162 of 8,085 open markets
   in the top-100 events are already past `endDate` and still accepting orders — an
   EPL match with `endDate` 15:30Z keeps trading while the match is played. Treating
   a past `endDate` as "resolving now" would have inverted the ranking toward
   exactly the in-play sports whose horizon we cannot see. They are now counted
   separately as `share_past_scheduled_end` and excluded from the short share.

3. **`order=volume24hr` systematically hides the entire short-duration lane.** A
   5-minute market lives five minutes, so its 24h volume is negligible and it never
   enters the top 1,200 events. The first scan saw **zero live 5-minute markets**
   and described crypto as a ~5-day-median-horizon category — the opposite of the
   lane we actually trade and the lane in the 09-13 screenshot. The scan now unions
   two walks. The second must be floored at now (`end_date_min`): unfloored,
   `endDate`-ascending returns the *oldest*-ending markets, which are long-dead
   5-minute markets still flagged `closed=false` (`eth-updown-5m-1766161800`, from
   December, `acceptingOrders=None`, 0/1 book). 81 of 91 updown markets in that
   walk were stale.

4. **`acceptingOrders && enableOrderBook` is not a liveness filter.** 3,579 of
   football's 8,486 such markets quote **one side only**, and a market with no bid
   (or no ask) cannot host a two-legged pair at all. Including them moved the
   football median spread from 0.35 to 0.88 and tennis from 0.11 to 0.61. Liveness
   now requires both sides; the one-sided share survives as its own funnel metric.

**Venue caveat, stated once and applying to everything below:** gamma is
polymarket**.com**. Our executor trades polymarket **US**. Category structure
transfers; a specific market slug does not. Nothing here asserts that a `.com`
market is executable by our account.

---

## 1. Venue-wide, by category

`2sided` = live with both sides quoted. `1side` = share of book-enabled markets
quoting one side only. `horiz_m`, `<=1h`, `<=6h` are over `known` (pre-scheduled-end)
rows only. `past_end` = share still accepting orders past `endDate` — horizon not
observable. `score` is structural; see §3.

| category | mkts | 2sided | 1side | spread | liq $ | horiz_m | known | ≤1h | ≤6h | past_end | rwd | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hockey | 97 | 53 | 0.45 | 0.0020 | 41,008 | 418,218 | 51 | 0.000 | 0.000 | 0.038 | 1.00 | 0.791 |
| basketball | 179 | 162 | 0.10 | 0.0095 | 11,391 | 374,778 | 132 | 0.000 | 0.000 | 0.000 | 1.00 | 0.757 |
| politics | 3,692 | 3,018 | 0.18 | 0.0140 | 9,226 | 63,019 | 2,850 | 0.000 | 0.000 | 0.035 | 0.98 | 0.737 |
| crypto | 2,218 | 2,127 | 0.04 | 0.0100 | 3,274 | 459 | 2,123 | 0.067 | 0.389 | 0.000 | 0.99 | 0.648 |
| economics | 204 | 193 | 0.05 | 0.0100 | 1,818 | 46,698 | 193 | 0.000 | 0.000 | 0.000 | 0.96 | 0.532 |
| business | 1,637 | 1,374 | 0.16 | 0.0180 | 1,182 | 69,738 | 1,367 | 0.000 | 0.000 | 0.000 | 0.98 | 0.452 |
| weather | 1,527 | 701 | 0.54 | 0.0200 | 1,290 | 1,099 | 571 | 0.007 | 0.007 | 0.185 | 0.99 | 0.452 |
| soccer | 12,099 | 9,355 | 0.23 | 0.0300 | 1,468 | 289 | 7,836 | 0.159 | 0.560 | 0.156 | 1.00 | 0.450 |
| culture | 765 | 670 | 0.12 | 0.0180 | 1,112 | 127,098 | 663 | 0.000 | 0.000 | 0.010 | 0.99 | 0.447 |
| baseball | 880 | 739 | 0.16 | 0.0300 | 844 | 339 | 641 | 0.281 | 0.562 | 0.133 | 0.99 | 0.430 |
| other | 132 | 121 | 0.08 | 0.0210 | 814 | 34,938 | 121 | 0.000 | 0.000 | 0.000 | 0.99 | 0.412 |
| tennis | 685 | 500 | 0.27 | 0.1700 | 269 | 9,979 | 458 | 0.000 | 0.000 | 0.010 | 1.00 | 0.019 |
| football | 11,172 | 6,557 | 0.41 | 0.3300 | 200 | 164 | 4,819 | 0.000 | 0.550 | 0.218 | 0.96 | 0.014 |
| esports | 1,186 | 768 | 0.35 | 0.2195 | 137 | 1,159 | 665 | 0.012 | 0.244 | 0.052 | 1.00 | 0.012 |
| sports_other | 643 | 366 | 0.43 | 0.9000 | 16 | 9,259 | 363 | 0.000 | 0.000 | 0.008 | 0.99 | 0.001 |

Notes that matter more than the ordering:

- **Market count and quality are close to inversely related.** Football and soccer
  supply 23,271 of 37,116 open markets — 63% of the venue — and sit at the bottom on
  both spread and depth. Ranking by breadth of slate would have selected precisely
  the categories where a pair cannot be assembled cheaply. This is the concrete form
  of the owner's "do not rank categories merely by number of markets".
- **The top four rows are top on depth and spread, and have no short-horizon lane at
  all** (`≤1h` = 0.000 for hockey, basketball, politics). They are capital-parking
  categories with 260-to-290-day median horizons, not turnover categories. Hockey's
  score rests on 53 live markets and should be read as a thin sample.
- **Crypto is the only category that is simultaneously tight (0.0100), non-trivially
  deep ($3,274), and short (38.9% resolving within 6h), with the lowest one-sided
  share on the venue (0.04).**

---

## 2. Short-horizon breakout — the ranking inverts here

Live, two-sided, and ≤60 minutes from the scheduled end: 1,581 markets. This is the
regime our engine and L5 actually operate in, and the venue-wide row understates it,
because crypto's venue-wide depth median is dragged down by the very 5-minute
markets that make the lane tradeable.

| category | mkts | median spread | median liq $ | median horizon | series |
|---|---|---|---|---|---|
| soccer | 1,246 | 0.0300 | 1,827 | 18.6 m | in-play spread/total/corner props |
| baseball | 180 | 0.1000 | 309 | 31.1 m | in-play spread/total props |
| **crypto** | **143** | **0.0100** | **2,456** | **28.6 m** | `*-updown-5m`, `*-updown-15m` |
| esports | 8 | 0.7300 | 10 | 11.1 m | CS2 per-game |
| weather | 4 | 0.0245 | 72 | 18.6 m | daily rain |

**In the short-horizon regime crypto is both the tightest and the deepest** — 3× the
depth of soccer at one third the spread — and it is the only category there whose
supply is a *scheduled recurring series* rather than the tail of a live event. The
lane is 8 assets (BTC, ETH, SOL, XRP, DOGE, BNB, ZEC, HYPE) × 5m, 12 markets each in
the capture window, plus the 15m variants.

That structural fact is consistent with the 09-13 screenshot (repeated 10/15-share
1¢ buys in a 5-minute BTC market) and with canonical L5's clip sizes, but **it is
structure, not evidence of expectancy**, and it is on `.com` rather than PMUS.

---

## 3. What "score" is, and what it is not

`score = 0.45·tight_spread + 0.35·depth + 0.20·short_share`, renormalised to
0.45/0.35 when horizon is unobservable (so an in-play category is not penalised for
a measurement gap it did not cause).

It ranks **where to look**. It is a structural proxy for three of the seven terms in
expected realized economics, and it **carries no residual term at all** — and the
residual is what decided both live losses (jayfri −$3.9506, mzp-ezt −$2.9077
directional against +$0.1755 matched). A category could top this table and still be
unprofitable for us if completion there is poor.

## 4. What this cannot answer

Gamma does not expose, and this scan therefore reports as `needs_execution_data` on
every single row:

first-leg maker fill frequency · complement maker fill frequency · time-to-complement ·
effective pair cost · share completing ≤1.00 and ≤0.99 · adverse movement after first
fill · residual frequency · residual loss severity · two-sided print frequency ·
trades per minute

**The owner's most important metric — expected realized economics = matched-pair
profit + directional residual P&L + confirmed incentives − fees — cannot be computed
from this capture.** Four of its five terms are in the list above. Producing a number
anyway would be the exact failure mode this file spends its first section documenting.

Those terms need either (a) a forward collector on the candidate lanes, or (b)
reconstruction from L5's own public trade history — which is P2 (L5 himself) and P3
(the completion environment: where P(completion | first fill) is higher and/or
E(residual loss | no completion) lower). **P3 is the question that decides breadth,
not this table.**

`rewards_programme_share` is near 1.00 venue-wide and is *stated programme
parameters only*. Per standing rule it is not incentive income until credited and
source-accounted, and it is excluded from the score.

## 5. Status

- P1 scan: **complete and regression-pinned**. Re-runnable; it is read-only by
  construction and a test forbids every order and deploy verb from the module.
- P2, P3: **open**.
- P4 breadth ramp: **not proposed.** Gated on another clean settled cycle, and on P3
  answering the completion question. One losing settled cycle is not a basis.
- Live engine: **untouched** throughout this work.
