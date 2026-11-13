# Railway deployment trigger

This file exists only to force a source-branch deployment after the initial Railway service creation snapshot unexpectedly resolved to `main` despite the service being configured for `feature/l5-transfer-backtest`.

The collector remains read-only and keyless. Deployment acceptance still requires the explicit collector Dockerfile, zero secrets, and a persistent volume mounted at `/state/pmus-sports` before durability is claimed.

## 2026-11-13 — theoretical liquidity-incentive recorder (read-only)

Branch `feat/pmus-sports-incentive-recorder-20261113`. Adds `src/pmus_incentive_recorder.py` (COPY'd above) and calls it from `src.pmus_sports_live_collector` behind `PMUS_INCENTIVE_RECORDER` (default `1`; `0` disables). It is READ-ONLY and keyless: public GETs to `gateway.polymarket.us` only (`/v1/incentives?symbols=...` every ~10 min for polled markets and their same-event siblings, and `/v1/markets/{slug}/bbo` for at most 3 settlement reads per cycle). It places no orders and needs no credentials. It reuses the L2 book the collector already fetches. It writes THEORETICAL rows (`program_map`, `incentive_share`, `touch_fill`, `markout`, `settlement`) to `/state/pmus-sports/incentive/pmus_incentive_YYYYMMDD.jsonl` (rotated daily, capped at 200k rows/day). It also prints a `PMUS_INCENTIVE_STATUS` line about every 5 min. Every recorder call is wrapped, so any recorder exception is counted and logged as an `error` row with stage `incentive_recorder` and never stops collection. **Not deployed by this change**: merging or redeploying is a separate, owner-approved step.
