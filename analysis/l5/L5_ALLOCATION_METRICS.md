# Allocation metrics — WIDE SCAN, NARROW CAPITAL, HIGH MOBILITY

_OWNER #167 comment 5586921649. measurement only. This module changes no allocation rule, no cap, no gate and no live decision._

## The principle was already the behaviour

- wide scan: select_candidate ranks the full admitted universe every cycle
- high mobility when flat: it takes admitted[0] with no memory of the previous pick, so there is no loyalty to hold on to
- narrow capital when holding: CAMPAIGN_HOLD:POSITION_HELD suppresses selection while inventory is open

## Measurements

| # | measurement | value |
|---|---|---|
| 1 | idle cycles with a candidate and no choice | 0 |
| 1 | idle share of selection span | 0.0 |
| 1b | cycles that chose a market and opened nothing | 582 |
| 1b | by reason | {'no_candidate': 578, 'complement_acceptance_unproven': 4} |
| 2 | markets that took inventory | 28 |
| 2 | median lockup (closed), s | 69.8 |
| 3 | switches while flat | 76 |
| 3 | switches while holding | 0 |
| 4 | completion probability | 0.6122 |
| 4 | median OPEN→COMPLETE, s | 29.0 |
| 5 | settled venue-confirmed markets | 6 |
| 5 | realized venue USD | -12.5082 |
| 6 | median rank1−rank2 score gap | 0.022 |
| 7 | capital locked, market-seconds | 10004.1 |
| 7 | USD opportunity cost | None |

_a pair_score is not a dollar. The conversion needs the realized-per-opportunity-score relationship, which needs the settled cohort minimums in L5_CONCURRENCY_SCALING_GATE_v1 (sha b86fe09178ea78a2). Reporting an unbacked dollar figure here would be exactly the kind of modelled number the owner excluded_
