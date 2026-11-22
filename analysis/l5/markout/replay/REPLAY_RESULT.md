# Edge-gate filter replay on our own PMUS sports fills, 14-18 Nov 2026

**What this is: a FILTER of the 1,016 fills that actually happened.** It is NOT a simulation of
what the new rule would have been filled on (the rule would have quoted different prices, and
would have kept bidding to complete pairs), and it is NOT live or realized profit of the new rule.
Only V0 is a realized result. 22 Nov 2026, read-only; public Polymarket-international data only.

## Summary table (settled trading, same fill set, USD)

| | V0 actual | V1 allowlist | V2 allowlist + gate | V3 gate, any market with fair |
|---|---|---|---|---|
| Markets with kept fills | 173 | 63 | 55 | 144 |
| Fills kept | 1,016 | 593 | 171 | 328 |
| Contracts | 1,900.97 | 787.33 | 198.76 | 619.90 |
| Dollars spent | 953.91 | 389.99 | 94.42 | 279.18 |
| Completed pairs (contracts) / P&L | 913.2 / -4.46 | 375.6 / +4.03 | 38.6 / +2.91 | 92.9 / +7.10 |
| Unpaired residual: markets / cost | 47 / 36.25 | 23 / 18.43 | 54 / 58.70 | 133 / 193.35 |
| Residual P&L (winning markets) | -36.25 (0) | -18.43 (0) | -23.61 (21) | -46.28 (52) |
| **Trading P&L before fees** | **-40.71** | **-14.40** | **-20.70** | **-39.18** |
| Fees (ESTIMATED allocation) | -4.36 | -3.76 | -0.96 | -1.04 |
| Trading P&L after fees (est.) | -45.07 | -18.16 | -21.66 | -40.22 |
| P&L per dollar spent | -4.3% | -3.7% | -21.9% | -14.0% |
| Bootstrap 95% CI, per dollar | -6.7% .. -2.1% | -9.5% .. +0.1% | -42.3% .. 0.0% | -29.8% .. +1.5% |
| Worst single market | cs2 brute-eid -6.00 | cs2 brute-eid -6.00 | cs2 baks-ill -8.60 | itfwo vikmor-enakoi -8.85 |
| Capital-hours ($ x h, see note) | 2,279 (on $823) | 748 (on $304) | 241 | 786 |
| Rewards paid on kept markets (UPPER bound, not in P&L) | 13.82 (15.63 on all 241 quoted) | 3.89 | 2.72 | 12.14 |

Rows above "Fees" for V0 are MEASURED; everything in the V2 and V3 columns is ESTIMATED.

## MEASURED

- **V0 reproduces the reconciliation exactly**: 1,900.97 contracts, $953.91, pairs -4.46,
  residual -36.25, trading before fees **-40.71**; with the reconciled fees (-4.36) **-45.07**.
- **V1** (cfb, cs2, nba, wta; no nfl/sea market was traded) keeps every historical fill on 63
  markets: -14.40 before fees. It is a subset of V0, so no model is involved. Per dollar it is not
  different from V0 (-3.7% vs -4.3%; paired bootstrap difference -3.3 .. +2.9 points).
- Rewards actually paid: $15.63 on the 241 markets. On the allowlist leagues only $3.93.
- **Coverage (of 241 markets quoted, 173 with fills):**
  - International match found: **213 markets** (153 of the 173 filled); **939 of 1,016 fills
    (92.4%)**, **$822.80 of $953.91 (86.3%)**.
  - Price history served for closed markets: yes, 1-minute points, for all 213 matched markets.
    A usable point within 5 minutes before the fill: **933 fills (91.8%), $816.32 (85.6%)**;
    6 fills on matched markets had only stale points. 147 markets have a fair value on every
    fill, 153 on at least one. Median age of the point used: 35 s.
  - YES orientation: established by construction for all 213 (same slug order, or both player /
    team names matched; 3 are reversed-order matches mapped by name). Checked against the PMUS
    settlement on every matched market that PMUS settled with unpaired contracts:
    **38 of 38 agree, 0 disagree.** Second check, fill prices against both orientations: the
    chosen one fits clearly better in 111 of 153 markets, the flipped one in 1 (a 4-fill ATP
    market near 50/50, difference 3 cents); the rest are near 0.50 and uninformative.
  - Match method: 70 exact slug, 130 by player names (tennis, table tennis, doubles), 10 esports
    by team code with a numeric suffix on the international slug, 3 soccer outcome markets.
  - **Leagues with no international counterpart found:** utr (3 markets), acb (1), testcr (1),
    ligaf (1), lexp (1), engnl (1), plus the two crypto markets (not sports). Partly unmatched:
    cs2 25/32, dota2 5/10, setkameua 35/37, itfwo 30/31, itfme 20/21, wta 2/3, atpdb 2/3.
    The unmatched wta market holds 10 of the 12 wta fills.
- 39 of the matched markets (all table tennis: setka*, wtt) show zero or no international volume;
  their "fair value" is a quote on an untraded book.

## ESTIMATED

- **Fair value** = the international 1-minute price-history point at or before the fill (YES
  token; NO = 1 - YES). It is the venue's price series, taken as the mid; it cannot see moves
  inside the minute. Every V2/V3 number depends on it.
- **Gate outcome on the 933 fills with a fair value:** 328 accepted (OPEN 182 of 259, ADD 74 of
  256, lighter side 72 of 418). Rejections: edge < 0 on 255, edge < risk allowance 51,
  pair price > 0.97 on 132, both edge < 0 and pair > 0.97 on 167; no fair value 83.
- **The dominant effect is an artifact of filtering, not evidence about the rule.** The gate
  rejects 83% of the fills that arrive on the lighter side of the simulated position (346 of
  418; 299 of them fail the 0.97 pair cap), and in a filter nothing replaces them, so accepted
  first legs stay unpaired: residual inventory rises from
  $36 in 47 markets (V0) to $59 in 54 of 55 markets (V2) and $193 in 133 of 144 markets (V3).
  V2/V3 settled P&L is therefore mostly the outcome of 54 / 133 unhedged one-sided positions
  (21 / 52 won), which the live rule would have continued to work with cheaper completion bids.
- **Differences are not distinguishable from noise** (paired bootstrap over the 173 markets,
  10,000 resamples, P&L per dollar): V2 - V1 = -39.4 .. +4.7 points; V3 - V0 = -25.3 .. +6.3;
  V2 - V0 = -38.7 .. +4.3; V3 - V2 = -12.5 .. +26.7. The gated variants are worse per dollar
  in 89-94% of resamples, so the lean is negative, but every interval includes zero. In dollars: V2 - V1 = -36.0 .. +19.8;
  V3 - V0 = -44.4 .. +47.0. Only V1 - V0 in dollars is clearly positive (+15.1 .. +39.4), and
  that is simply the loss on the 110 excluded markets.
- **Short-horizon check (more statistical power than settlement, same price source):** value of
  fills against the international price at the fill, 1 minute and 5 minutes later:

  | Fills with fair value | n | at fill | +1 min | +5 min | mean at +5 min |
  |---|---|---|---|---|---|
  | Gate accepted (V3) | 328 | +36.18 | +17.86 | +7.32 | +0.0 ticks |
  | Gate accepted, allowlist (V2) | 171 | +8.64 | +2.52 | -2.43 | -1.4 ticks |
  | Gate rejected | 605 | -14.31 | -25.53 | -33.37 | -2.1 ticks |
  | All | 933 | +21.87 | -7.67 | -26.06 | -1.4 ticks |

  The gate does separate fills (accepted about 0 ticks after 5 minutes, rejected about -2), but
  the edge it measures at the fill (+$36) is mostly gone within 5 minutes: the 1-minute price was
  stale against us at the moment of the fill. On the allowlist leagues the accepted fills are
  slightly negative after 5 minutes. None of this is profit.
- **Fees.** The fill log carries no fees. Shown: each market's taker-fee estimate from the P&L
  file (sum 4.87) scaled to the reconciled 4.36, then pro-rata by contracts kept in that market.
  Plain pro-rata by contracts over all fills gives V1 -1.81, V2 -0.46, V3 -1.42. The new rule is
  maker-only, so its own fees would differ.
- **Capital-hours** = fill dollars x hours from the fill to the international market's close
  (resolution) time. PMUS settlement time is not in the data; unmatched markets are excluded
  (V0 covers $823 of $954, V1 $304 of $390); no credit for capital freed when a pair nets.
- **Rewards** per variant are an upper bound: the amounts actually paid on markets where the
  variant keeps at least one fill. Quoting less would have earned less. Of V3's $12.14, $9.12 is
  on markets with under $1,000 international volume (table tennis), where the fair value is
  weakest. Sensitivity "V3_liq" (V3 only where international volume is at least $1,000):
  100 markets, 258 fills, $202.03, P&L -26.49 (-13.1%, CI -30.6% .. +5.5%), rewards 3.02.
- Jump ticks follow the stated list literally: soccer 3, nhl 2, cfb/nfl 1, atp/wta/itf* 1,
  setka* 1, everything else 0 (so atpdb, wtadb, wtt and esports use 0). Clip = the order's
  quantity. A fill larger than the lighter-side deficit is still treated as lighter-side.
  Pair/residual split uses average cost per side, as the reconciliation did.
- Settlement of simulated residuals uses the PMUS settlement where recorded, otherwise the
  resolved international market (same winner in all 38 overlapping cases).

## NOT AVAILABLE

- What the new rule would actually have been filled on: its own bid prices, queue position,
  later completion fills at pair cost <= 0.97, cancels on price moves. Not recoverable from fills.
- Fair value for 83 fills ($137.59): 77 on 20 filled markets without an international match,
  6 stale. These are rejected in V2/V3 by rule, not because they were judged bad.
- Sub-minute international prices or order books at the fill time; PMUS outcome names (no PMUS
  call was made, so orientation rests on slug order plus the two checks above).
- Per-fill fees, PMUS settlement timestamps, and rewards under reduced quoting.
- A sample large enough to rank the variants: 55-173 markets over five days, with one market
  able to move a variant by $6-9.

## Reading

1. As a filter, the gate cuts dollars at risk by 71-76% but does not improve settled P&L per
   dollar; the result is dominated by unpaired first legs and is statistically indistinguishable
   from V0/V1.
2. The useful signal is the short-horizon table: the gate removes fills that lose about 2 ticks
   in 5 minutes, and what it keeps is about break-even against the international price, before
   any pairing benefit or reward.
3. The allowlist alone (V1) removed $26 of the $41 loss, while keeping only $3.9 of the $15.6
   rewards; adding rewards on kept markets, V0 is about -29.4 and V1 about -14.3 (after est. fees).

## Files (all in this directory)

`http_util.py` (rate-limited cached GET), `fetch_series.py` -> `intl_events.json`,
`resolve_intl.py` -> `market_map.json`, `fetch_history.py` -> `price_history.json`,
`replay.py` -> `replay_fills.csv`, `replay_summary.json`. Re-run in that order; `cache/` holds
the raw public responses, so a re-run makes no network calls unless the cache is removed.
