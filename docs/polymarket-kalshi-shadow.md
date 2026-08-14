# Polymarket US ↔ Kalshi shadow scanner

This branch contains the read-only cross-exchange MLB full-game-total scanner used by the isolated Railway service `polymarket-kalshi-live-shadow`.

Safety properties:

- No order submission, modification, or cancellation code in the shadow scanner.
- $50 is a shadow sizing ceiling only; no capital is placed.
- Market matching fails closed unless team pair, game date, total threshold, and full-game market type align unambiguously.
- Both sides are priced at executable top-of-book asks and capped by visible top-of-book depth.
- Fee rates are explicit runtime inputs.

The service logs discovery, periodic summaries, and OPEN/MOVE/CLOSE opportunity lifetimes for later execution analysis.
