# Weather Shadow Integration

Status: READ-ONLY / SHADOW ONLY

## Goal
Bring objective-settlement Kalshi weather markets into the ARB architecture without inheriting legacy execution assumptions from `unbuiltworks/kalshi-weather-bot`.

## Legacy inputs we may reuse
- GFS / ECMWF / ICON forecast ingestion
- NWS / NOAA station mappings
- Kalshi weather series discovery knowledge
- Existing weather settlement and backtest datasets as research inputs only

## Hard gates before any live probe
1. Current Kalshi API field audit (dollar fields, fixed-point volume/depth, market status)
2. Exact series/market identity and event date
3. Exact settlement station/source and local-day definition
4. Exact bracket boundaries, rounding/tie behavior, and correction/revision policy
5. Executable bid/ask/depth and quote freshness
6. Current fee model
7. Shadow opportunity logging into shared ARB ledger/reporting schema
8. Official settlement reconciliation with zero rule mismatches
9. Small current sample of clean shadow outcomes before requesting a <=$5 live micro-probe

## Required shadow record
- category=weather
- classification=SHADOW or PAPER
- venue
- series_ticker / event_ticker / market_ticker
- city / station
- settlement_source
- target_date
- bracket / side
- model_probability
- executable_price
- available_depth
- modeled_fees
- modeled_after_fee_ev
- quote_timestamp / quote_age_ms
- forecast_timestamp / forecast_age
- rule_certification_status
- settlement_status
- realized_pnl only after official settlement

## Legacy findings that must NOT be treated as production truth
- Conflicting historical win-rate claims
- Abandoned harvest strategy; historical backtest showed a large loss
- Any results produced before the Kalshi dollar-field/API normalization fix
- Any paper/shadow P&L not reconciled to official settlement

## First target
Current daily high-temperature markets with objective NWS/NOAA settlement. Prefer the smallest set of cities whose station/rules can be independently certified quickly. No order entry is enabled by this branch.
