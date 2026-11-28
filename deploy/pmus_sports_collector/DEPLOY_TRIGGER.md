# Railway deployment trigger

This file exists only to force a source-branch deployment after the initial Railway service creation snapshot unexpectedly resolved to `main` despite the service being configured for `feature/l5-transfer-backtest`.

The collector remains read-only and keyless. Deployment acceptance still requires the explicit collector Dockerfile, zero secrets, and a persistent volume mounted at `/state/pmus-sports` before durability is claimed.
