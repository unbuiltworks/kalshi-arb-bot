# Sports live-canary rollout checkpoint — 2026-08-19

- Production sportsbook scanner filters Odds API events at/after first pitch.
- Direct Gemini/Kalshi MLB hit-prop hot shadow remains event-driven and read-only.
- Live rule-risk canary remains one-shot, one contract per leg, $5 hard service ceiling.
- Authorization is deadline-gated and must not be counted as guaranteed arbitrage.
- This branch is isolated from the production sports-alert branch so canary changes do not restart the main sports scanners.
- Isolated canary-specific runtime validation requested before live arming.
