# MLB full-game rule certification

Audit date: 2026-08-18

This registry is a read-only safety gate for sportsbook-vs-prediction-market and prediction-vs-prediction alerts. It does not place orders and does not make a positive price discrepancy execution-ready by itself.

## Certification rule

`CERTIFIED` means the full settlement lifecycle is equivalent, including normal outcome, extra innings, minimum-game-completion requirements, game-not-started handling, interruption/postponement, cancellation, tie/push behavior, and official-result authority. Only `CERTIFIED` pairs may be upgraded to `FULL_RULES_AUDITED_EXECUTABLE` for the Pushover qualification path.

`CONDITIONAL`, `REJECTED`, and `UNAUDITED` remain notification-blocked for actionable arb alerts.

## Audited full-game families

The registry now covers:

- moneyline
- spread / run line
- game total

For each family it audits six sportsbook-vs-prediction pairs (FanDuel and DraftKings against Kalshi, Polymarket, and Gemini) plus the three direct prediction-market pairs (Kalshi/Polymarket, Kalshi/Gemini, Polymarket/Gemini).

## Current result

No audited pair is currently certified.

Sportsbook spread and total markets have a material completion-rule difference from the prediction markets reviewed: DraftKings and FanDuel generally require nine innings, or 8.5 innings with the home team leading, unless the result is already unconditionally/unequivocally determined. Their moneyline rules can become official after five innings (or 4.5 with the home team leading). Prediction-market contracts use different lifecycle rules and do not share those sportsbook void semantics.

The direct prediction-market pairs are closer in ordinary completed games, but still fail exact settlement equivalence:

- Kalshi can keep a postponed game open for a rescheduled completion within two days and uses a fair-price branch if it is cancelled or rescheduled farther out.
- Polymarket keeps postponed MLB game markets open until completion and resolves a fully cancelled/no-makeup game 50-50; its resolution-source fallback can also use credible reporting after the official-statistics window.
- Gemini uses MLB/ESPN/Fox/broadcast-partner source ordering and can route an indeterminate expiration value through Market Outcome Review.

Those differences mean an apparent two-leg lock can still break in an exceptional game lifecycle. They remain `CONDITIONAL`, not execution-certified.

## Sources reviewed

- DraftKings Baseball Rules: https://sportsbook.draftkings.com/help/sport-rules/baseball
- FanDuel Sportsbook House Rules (baseball section): https://www.fanduel.com/fanduel-sportsbook-house-rules-ny
- Kalshi MLB game/spread/total market rules and current event pages: https://kalshi.com/markets/kxmlbgame/professional-baseball-game
- Polymarket MLB game-line rules: https://polymarket.com/sports/mlb
- Gemini Predictions MLB contracts and Prediction Markets API/taxonomy: https://developer.gemini.com/prediction-markets-spec/markets

## Fail-closed behavior

Unknown books and unaudited market families return `UNAUDITED`. A certification registry result is a second independent gate: a lock must both carry `FULL_RULES_AUDITED_EXECUTABLE` confidence and have a `CERTIFIED` registry assessment before Pushover can treat it as actionable.
