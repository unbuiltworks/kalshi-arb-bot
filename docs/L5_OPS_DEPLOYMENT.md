# L5 Ops — Safe Activation Topology (issue #167)

Status: **PROPOSED, NOT ACTIVATED.** Activation is a single owner-side
action described below; nothing in this repo activates on its own.

## Current live state (re-established 2026-09-01, from `origin/main`)

* **Railway**: ONE service. `.github/workflows/deploy.yml` runs
  `railway up --service $RAILWAY_SERVICE_ID` on **every push to `main`**
  (no path filter). The service builds the ROOT `Dockerfile`, which runs
  the trading bot (`python -m src`, `BOT_MODE` paper/live).
  ⇒ Confirmed: any push to `main` — including a docs-only merge —
  redeploys the trading service. **Therefore: no main pushes for this.**
* **GitHub Actions**: all other 15 workflows trigger on `issues: opened`
  only. There are **no** scheduled workflows and none that fire on pushes
  to research or data branches.
* **AWS**: not referenced by any workflow in this repository. The Kalshi
  canary is deployed separately (release branches, operator-managed) and
  is unreachable from anything here.

## Recommended topology: isolated Railway research service

```
┌──────────────────────── Railway project ────────────────────────┐
│                                                                 │
│  [existing trading service]           [NEW: l5-ops-research]    │
│   builds  /Dockerfile                  builds /research/Dockerfile
│   deploys on push to main              deploys on push to        │
│   env: exchange credentials            feature/l5-canonical      │
│   UNTOUCHED by this proposal           env: GIT_PUSH_TOKEN only  │
│                                        (repo-scoped, no exchange │
│                                         credentials)             │
└─────────────────────────────────────────────────────────────────┘
            code                                 state
  feature/l5-canonical  ──build──▶  runner ──commits──▶  l5-ops-data
  (pinned research branch)          loop                (data-only branch)
```

* **Code**: the new service builds `research/Dockerfile` from
  `feature/l5-canonical`. That image contains the ops layer, the frozen
  baselines, and `scripts/l5_ops_runner.sh` — and none of the trading
  entrypoints' credentials. The runner loop: `ops collect` + `ops shadow`
  every 30 min (`OPS_INTERVAL_SECONDS=1800`, floor 600), `ops regression`
  once per UTC day, heartbeat + failure artifacts as before.
* **State**: `data/l5/ops` is restored at startup from, and committed
  after every cycle to, the **data-only branch `l5-ops-data`** (already
  created at `f6d22d2`: README only — no code, no workflows). Pushing
  data there triggers no workflow and no deploy, and because the service
  watches the CODE branch, data pushes cause no redeploy loop.
  Restart/container-replacement ⇒ resume from last committed cursor
  (verified end-to-end against a local origin: restore → cycle → snapshot
  push, including push-retry with rebase).
* **Credentials**: exactly one — `GIT_PUSH_TOKEN`, a fine-grained GitHub
  token, **Contents: Read/Write on this repository only**. It is not an
  exchange credential; the runner stores it in a git credential file and
  never echoes it. Without it the runner still operates (container-local
  state) and says so in its logs.

### Why not the alternatives

| Option | Verdict |
|---|---|
| Merge `l5-ops.yml` to `main` | ✗ the merge push itself triggers an unrelated Railway trading deploy (no path filter) |
| Add a path filter to `deploy.yml` | ✗ modifies the live deploy workflow — owner said only if no safer alternative; there is one |
| Scheduled workflow on a non-default branch | ✗ GitHub only runs schedules from the default branch |
| AWS scheduler (EventBridge/ECS or cron+systemd) | ✓ workable — same image/runner runs anywhere Docker or bash exists (`OPS_ONESHOT=1` per cron tick). Kept as documented fallback; Railway preferred because the project already lives there and isolation is a first-class service boundary |
| Railway cron service (`OPS_ONESHOT=1`) | ✓ workable variant of the recommendation; the loop service is simpler and carries its own heartbeat |

## Activation steps (owner-side, ~5 minutes, no repo changes)

1. Railway dashboard → the existing project → **New Service → GitHub repo
   → this repo**.
2. Set **Branch: `feature/l5-canonical`** and **Dockerfile path:
   `research/Dockerfile`** (service settings → Build).
3. Add env var `GIT_PUSH_TOKEN` = a fine-grained PAT scoped to this repo,
   Contents R/W. (Optionally `OPS_INTERVAL_SECONDS`, default 1800.)
4. Do **not** attach any shared variables from the trading service.
5. Deploy. Nothing else changes: no push to `main` occurs, the trading
   service is not rebuilt, `deploy.yml` does not run.

## Verification checklist (prove live services unchanged)

Before activation, record; after activation, re-check:

* [ ] `git log origin/main` — unchanged (no new commits).
* [ ] Actions tab — no `Deploy to Railway` run triggered.
* [ ] Railway: existing trading service shows **no new deployment** and
      unchanged env; the only new object is the `l5-ops-research` service.
* [ ] AWS/canary: untouched by construction (nothing here references it).
* [ ] New service logs show: `starting: … interval=1800s`, `code version:
      <sha>`, `restored N state files from l5-ops-data` (or "starting
      fresh" on first boot).
* [ ] Within ~35 min: a `l5-ops: state snapshot …` commit appears on
      `l5-ops-data`, and `data/l5/ops/heartbeat.json` there shows
      `status: ok` with a small `cursor_lag_s`.

Health monitoring afterwards = read `l5-ops-data`:
`heartbeat.json` (status/lag), `failures/` (retained artifacts),
`regression_YYYYMMDD.json` (daily check). A stale heartbeat (> ~2
cycles) means the service is down — restart it in Railway; state resumes
from the branch.

## Rollback

Delete (or pause) the `l5-ops-research` service in Railway. Nothing else
depends on it; the trading service, `main`, and all workflows are
untouched. The `l5-ops-data` branch remains as the audit trail.

## Pinned versions (fill at activation)

* Code branch: `feature/l5-canonical` @ `<sha reported in #167>`
* Data branch: `l5-ops-data` @ `f6d22d2` (initial)
* Runner: `scripts/l5_ops_runner.sh`; image: `research/Dockerfile`
