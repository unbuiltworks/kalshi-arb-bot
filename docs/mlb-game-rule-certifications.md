# MLB full-game rule certification

Audit date: 2026-08-18

This registry is a read-only safety gate for sportsbook-vs-prediction-market alerts. It does not place orders and does not make a positive price discrepancy execution-ready by itself.

## Certification rule

`CERTIFIED` means the full settlement lifecycle is equivalent, including normal winner, extra innings, game-not-started, interruption/postponement, cancellation, tie/push, and official-result authority. Only `CERTIFIED` pairs may be upgraded to `FULL_RULES_AUDITED_EXECUTABLE` for the Pushover qualification path.

`CONDITIONAL`, `REJECTED`, and `UNAUDITED` remain notification-blocked for actionable arb alerts.

## Current full-game moneyline result

No audited sportsbook/prediction-market pair is currently certified.

- DraftKings vs Kalshi: not certified. DraftKings has void branches for games that do not start / fail its completion window; Kalshi can remain open for a rescheduled game and uses a fair-price branch for cancellation or rescheduling beyond its window.
- DraftKings vs Polymarket: not certified. DraftKings void branches differ from Polymarket's remain-open / 50-50 cancellation behavior.
- DraftKings vs Gemini: not certified. DraftKings void behavior is not identical to Gemini's Market Outcome Review path when an expiration value cannot be determined.
- FanDuel vs Kalshi / Polymarket / Gemini: not certified. Sportsbook official-game, suspension and void semantics are not identical to the prediction-market exceptional-event branches.

These pairs may still be economically comparable in ordinary completed games; that is not enough to label them risk-free arbitrage.

## Sources reviewed

- DraftKings Baseball Rules: https://sportsbook.draftkings.com/help/sport-rules/baseball
- DraftKings General Rules: https://sportsbook.draftkings.com/help/general-betting-rules
- Kalshi KXMLBGAME market rules: https://kalshi.com/markets/kxmlbgame/professional-baseball-game
- Polymarket MLB market rules: https://polymarket.com/sports/mlb
- Gemini Prediction Markets / MLB contract rules: Gemini prediction-market product and contract rules
- FanDuel: official sportsbook house/baseball rules applicable to the offered market/jurisdiction

## Fail-closed behavior

Unknown books and unaudited market families return `UNAUDITED`. A certification registry result is a second independent gate: a lock must both carry `FULL_RULES_AUDITED_EXECUTABLE` confidence and have a `CERTIFIED` registry assessment before Pushover can treat it as actionable.
