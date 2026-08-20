# NFL G/K dynamic hot v2 validation — 2026-08-20

Read-only regression gate for diversified game/family selection plus q=1 depth measurement independent of profitability. V2 also separates 50ms strict economics from 100ms and 250ms diagnostic tiers. No order-entry code is present.

Regression passed 3/3. Railway is configured for a 180-second read-only scan of 180 exact future G/K propositions, capped at 9 contracts per game, with 50ms strict and 100ms/250ms diagnostic economics.
