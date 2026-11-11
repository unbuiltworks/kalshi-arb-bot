# Permanent Railway service isolation (issue #167 comment 5521210994)

Two Railway services build from this branch and MUST never cross-build:

| service                      | Dockerfile                              | start command                                | role env (baked in image) |
|------------------------------|-----------------------------------------|----------------------------------------------|---------------------------|
| `pmus-sports-forward-collector` | `deploy/pmus_sports_collector/Dockerfile` | `python3 -m src.pmus_sports_collector`       | `PMUS_SERVICE_ROLE=collector`   |
| `pmus-ws-evidence-recorder`     | `deploy/pmus_ws_recorder/Dockerfile`      | `python3 -m src.pmus_ws_evidence_recorder`   | `PMUS_SERVICE_ROLE=ws-recorder` |

## The supported permanent pin (current Railway)

Railway has deprecated `railway.json` Config-as-Code (the legacy
Config File Path mechanism that caused the collisions). The CURRENT
supported per-service pin is the **service variable**:

    RAILWAY_DOCKERFILE_PATH=/deploy/pmus_ws_recorder/Dockerfile      # recorder
    RAILWAY_DOCKERFILE_PATH=/deploy/pmus_sports_collector/Dockerfile # collector

Service variables live in Railway service settings, NOT in the repo, so
a branch push cannot change or cross them. The operator set the recorder
pin on 2026-09-03; the collector service must carry its own pin the same
way. Volumes (`/state/pmus-ws`, `/state/pmus-sports`), the source
branch, and start commands are unchanged.

`.railway/railway.ts` IaC was deliberately NOT adopted here: the
service-variable pin is the venue-documented mechanism, requires no new
schema, and keeps zero deployment config in a branch that agents push
to at high frequency. If repo-side IaC is later required, it replaces
this file in one reviewed change.

## The repo-side tripwire (defense in depth, branch-push-proof)

Both Dockerfiles now **bake their role** (`ENV PMUS_SERVICE_ROLE=...`).
Both workers assert the role at startup and exit loudly with
`SERVICE_ROLE_MISMATCH` when started as the wrong worker. So even if a
service pin were lost and Railway cross-built again, the wrong image
CRASHES visibly at boot instead of silently running the wrong worker or
restart-looping on a missing module. Structural tests pin the baked
roles, the distinct start commands, and that the recorder image carries
every module + model artifact its status emission imports.

## Acceptance checklist for a fresh recorder redeploy

1. Build log shows `deploy/pmus_ws_recorder/Dockerfile` as the build
   path (the recorder-only image installs `websockets`+`cryptography`
   and copies `pmus_ws_evidence_recorder.py` — the collector image does
   neither).
2. Runtime emits uninterrupted `PMUS_WS_STATUS` lines with `auth=ok`
   and no `SERVICE_ROLE_MISMATCH` / `No module named` restarts.
3. The collector service keeps its own image and heartbeat untouched.
