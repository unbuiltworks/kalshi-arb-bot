# MLB prop shadow diagnostics — 2026-08-16

Read-only evidence gathered before the 17:35–17:40Z game cluster.

- Polymarket US `/v2/leagues/mlb/events` returned 56 active MLB events and 507 nested markets at 15:06Z, but none were player props. The observed market types were full-game winner/spread/total and first-five spread/total. Therefore a zero strict player-prop pair count at that time means the target player-prop families were not posted on this endpoint yet, not that the strict player parser rejected them.
- Kalshi had identity-valid open player-prop markets in all four target series. The strict schedule guard had incorrectly treated the event-ticker clock as Eastern time even when a structured `market.occurrence_datetime` was present. Live payloads showed those clocks can disagree materially; structured occurrence time is now authoritative and ticker parsing is fallback-only.
- All diagnostics and the Railway `mlb-prop-shadow` service are read-only. No order-entry path, sizing, risk, or eligibility behavior is changed.
- Minute-level observations do not establish sub-second persistence.
