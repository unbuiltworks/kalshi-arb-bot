# Dynamic NFL G/K hot discovery validation — 2026-08-20

Read-only fail-closed tests for exact future Gemini/Kalshi NFL joins. Kickoff outliers, ambiguous venue identities, and past games must be rejected before any websocket subscription. No order-entry code is present.

Regression gate passed 4/4. Railway is configured to discover current exact G/K NFL identities, select the 120 nearest future propositions, shard both venue book streams, and evaluate q=1 after-fee economics from live books for 180 seconds. Rule status remains CONDITIONAL and order entry remains disabled.
