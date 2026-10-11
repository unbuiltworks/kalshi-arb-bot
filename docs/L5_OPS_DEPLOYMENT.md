# L5 Ops — Safe Activation Topology (issue #167)

Status: **PROPOSED, NOT ACTIVATED.** Activation is a single owner-side
action described below; nothing in this repo activates on its own.

## Current live state (corrected 2026-09-01 after connected Railway verification)

Two DIFFERENT views that must not be conflated:

**(a) What the repo's CI can deploy** (from `origin/main`):
`.github/workflows/deploy.yml` runs `railway up --service
$RAILWAY_SERVICE_ID` on **every push to `main`** (no path filter),
building the ROOT `Dockerfile` = the trading bot (`python -m src`,
`BOT_MODE` paper/live). It targets exactly ONE specific service (the ID
in the secret). ⇒ any push to `main` — even docs-only — redeploys that
trading service. **Therefore: no main pushes for this.**

**(b) What actually exists in Railway** (connected verification via the
owner/ChatGPT, 2026-09-01 — NOT inferable from the repo, and not
queryable from this environment, which holds no Railway credentials):
project **`arb-ev-wallet-shadow`** contains **11 services**, most with
latest deployments from ~Aug 27:

| Existing service | Workstream (by name ↔ repo modules) | Disposition for this activation |
|---|---|---|
| `polymarket-wallet-shadow` | older Polymarket wallet shadow | untouched |
| `polymarket-temporal-shadow-run` | older Polymarket temporal shadow | untouched |
| `polymarket-maker-econ-shadow` | older Polymarket maker-econ shadow | untouched |
| `polymarket-maker-ws-shadow` | older Polymarket WS shadow | untouched |
| `polymarket-l5-econ-ledger` | pre-canonical L5 ledger | untouched (retire candidate LATER, see below) |
| `polymarket-l5-replay-once` | pre-canonical L5 replay — latest deployment **CRASHED** | untouched; preserve until new service is clean |
| `gemini-maker-shadow` | Gemini maker research | untouched |
| `gemini-fair-value-shadow` | Gemini fair-value research | untouched |
| `kalshi-temporal-shadow` | Kalshi shadow collection | untouched |
| `kalshi-incentive-shadow` | Kalshi incentive watch | untouched |
| (empty temporal service, no latest deployment) | — | untouched |

Which of these (if any) is the live-adjacent trading service that
`deploy.yml` targets is determined by the `RAILWAY_SERVICE_ID` secret,
which is not readable from here — the activation gate below therefore
records ALL existing service+deployment IDs, so "nothing redeployed" is
proven for every service regardless of which one is the trading target.

* **GitHub Actions**: the other workflows trigger on `issues: opened`
  only; no scheduled workflows; nothing fires on pushes to research or
  data branches. `l5-canonical-ci.yml` (added on the research branch)
  runs the test suite on pushes to `feature/l5-canonical` only —
  read-only CI, no secrets, no deploys — so test results are
  independently produced by Actions rather than only locally.
* **AWS**: not referenced by any workflow in this repository. The Kalshi
  canary is deployed separately (release branches, operator-managed) and
  is unreachable from anything here. Untouched.

### Existing L5/Polymarket services: retire or reuse?

Assessed from repo evidence only (their connected branches/start commands
are visible only in the Railway dashboard — confirm there before acting):

* `polymarket-l5-econ-ledger` and `polymarket-l5-replay-once` are
  superseded by the canonical pipeline this branch carries (`ledger.py`,
  `ops.py`); the replay service is already crashed. **Recommendation:
  retire (do not reuse) AFTER `l5-ops-research` has run clean for a few
  days** — reuse would inherit unpinned pre-canonical code and settings.
  Not touched now, per review instruction.
* The four `polymarket-*-shadow` services may target other wallets or
  questions; the new ops collector supersedes only the L5-wallet shadow
  function. Inventory each service's source branch in the dashboard
  before retiring anything.
* Gemini/Kalshi services belong to other active workstreams — keep.

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

## Verification checklist (prove ALL existing services unchanged)

Target: project **`arb-ev-wallet-shadow`**, default environment, new
service name **`l5-ops-research`** — the ONLY object created.

Before activation, record; after activation, re-check:

* [ ] Railway: list all 11 existing services with their **service IDs and
      latest deployment IDs** (dashboard or `railway status` by the
      credential holder). After activation this list must be IDENTICAL —
      same deployment IDs, no restarts — with exactly one addition:
      `l5-ops-research`.
* [ ] `git log origin/main` — unchanged (no new commits).
* [ ] Actions tab — no `Deploy to Railway` run triggered.
* [ ] The crashed `polymarket-l5-replay-once` service is still present and
      still crashed (preserved, not repurposed).
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
