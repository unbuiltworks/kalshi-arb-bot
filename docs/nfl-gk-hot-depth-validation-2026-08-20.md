# NFL G/K hot depth validation — 2026-08-20

Read-only regression gate for the 14 fee-positive NFL Gemini/Kalshi research pairs. Validates q=1 fee/depth/skew and 50ms same-limit survival logic before any live market-data subscription. No order-entry code is present.

Regression: 4/4 probe tests passed, plus 37/37 existing branch build tests. Railway validator is configured for a 120-second live-book run using public Gemini bookTicker and authenticated read-only Kalshi orderbook_delta.
