# AWS ARB migration

This branch is intentionally based on the exact strategy commit currently used by Railway `sports-generic-counterpart-shadow`:

`044204d31c98a098a180e726899162180556ef63`

The first objective is **hosting parity**, not a strategy rewrite.

## Phase 0 — AWS shadow parity

Run the current sports maker delayed-hedge shadow on the existing `us-east-1` EC2 host while Railway continues running read-only.

Safety rules:

- AWS remains `BOT_MODE=paper` / read-only.
- Railway remains unchanged.
- Do not place orders from both platforms.
- Do not copy secrets into GitHub, shell history, SSM command text, logs, or this repo.
- The runner verifies that `src/`, `config/`, `requirements.txt`, and `Dockerfile` still match the Railway strategy commit before starting.

Known existing host:

- OS: Ubuntu 26.04 LTS
- repo: `/home/ssm-user/kalshi-arb-bot`
- secure env file: `/etc/arb/sports-maker-shadow.env`
- isolated worktree: `/opt/arb/aws-sports-maker-shadow`

### One-shot host preparation

The preparation step installs Docker, fixes runtime ownership, verifies strategy parity, and builds the AWS image **without starting the maker shadow**.

Run this through Systems Manager `AWS-RunShellScript`:

```bash
set -euo pipefail
REPO=/home/ssm-user/kalshi-arb-bot
sudo -u ssm-user -H git -C "$REPO" fetch origin aws-migration-foundation
sudo -u ssm-user -H bash -lc "git -C '$REPO' show origin/aws-migration-foundation:deploy/aws/prepare_hot_host.sh > /tmp/arb-prepare-hot-host.sh"
chmod 0755 /tmp/arb-prepare-hot-host.sh
bash /tmp/arb-prepare-hot-host.sh
```

The bootstrap supports Ubuntu 26.04 using Docker's official apt repository. Git always runs as `ssm-user`; the repo is not marked globally safe for root. A dedicated `arb` group protects runtime files and `/opt/arb` is writable by the runtime user.

Expected successful ending before secrets are configured:

```text
BOOTSTRAP_OK
ENV_MISSING
STRATEGY_PARITY_OK commit=044204d31c98a098a180e726899162180556ef63
IMAGE_BUILD_OK image=arb-sports-maker-shadow:aws-parity
PREPARE_ONLY_OK
ENV_MISSING
AWS_HOT_HOST_PREPARED
NEXT_GATE=secure_env
```

`ENV_MISSING` is expected at this stage.

Required environment-variable names for the current Railway workload:

- `ARB_LEDGER_URL`
- `CENSUS_DEPLOY_TRIGGER`
- `KALSHI_API_KEY_ID`
- `KALSHI_ENV`
- `KALSHI_PRIVATE_KEY_PEM`
- `LEDGER_INGEST_TOKEN`
- `POLYMARKET_KEY_ID`
- `POLYMARKET_SECRET_KEY`
- `SPORTS_POLY_RELAY_LIVE_Q1`

Prefer AWS Secrets Manager for permanent deployment. If an env file is materialized on the parity host, it must be owned `root:arb` and mode `0640`; never paste its values into SSM Run Command.

### Start the read-only shadow after the secure-env gate

```bash
cd /home/ssm-user/kalshi-arb-bot
git fetch origin aws-migration-foundation
git show origin/aws-migration-foundation:deploy/aws/run_sports_maker_shadow.sh > /tmp/run_sports_maker_shadow.sh
chmod +x /tmp/run_sports_maker_shadow.sh
/tmp/run_sports_maker_shadow.sh
```

The runner forces `BOT_MODE=paper` even if the environment file says otherwise.

Inspect:

```bash
docker ps --filter name=arb-sports-maker-shadow-aws
docker logs -f arb-sports-maker-shadow-aws
```

Stop:

```bash
docker rm -f arb-sports-maker-shadow-aws
```

## Decision output

Compare AWS and Railway using the same strategy code and overlapping live market window. Capture at minimum:

- simulated pessimistic fills;
- Polymarket q1 full-ladder executable coverage;
- immediate P&L;
- 80 ms P&L;
- 250 ms P&L;
- 500 ms P&L;
- fill-detection-to-hedge-submit latency once order-entry plumbing is measured;
- p50 / p95 / p99 latency, not just averages.

Do **not** infer live profitability from venue ping or REST response time alone.

## Phase 1 — hot execution plane

Only after AWS parity is measured:

1. Create a dedicated ECR repository for the ARB image.
2. Use GitHub Actions OIDC to assume a narrowly scoped AWS deployment role; do not store long-lived AWS keys in GitHub.
3. Run latency-critical Kalshi/Gemini/Polymarket WebSocket book state, fill detection, reservations, and hedge submission on a dedicated `us-east-1` EC2/ECS-EC2 host.
4. Store venue credentials in AWS Secrets Manager and inject them at container start.
5. Send container logs/metrics to CloudWatch.
6. Keep the ledger/database unchanged initially and off the synchronous hedge path.

## Phase 2 — supporting services

After the hot path is stable, migrate only services classified CORE or ACTIVE SHADOW. Batch/scanner/reconciler workloads can use ECS/Fargate or scheduled ECS tasks. Do not recreate old one-offs, superseded services, or crashed experiments merely because they exist on Railway.

## Cutover rule

For any live strategy:

1. Disarm Railway.
2. Reconcile open orders, fills, reservations, and balances.
3. Confirm no ambiguous state remains.
4. Start AWS read-only.
5. Verify books, balances, ledger connectivity, rules, and alerts.
6. Arm AWS at q1 only after the strategy's existing acceptance gates pass.
7. Never run two independently armed executors for the same strategy.
