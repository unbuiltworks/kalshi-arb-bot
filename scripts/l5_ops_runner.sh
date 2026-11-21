#!/usr/bin/env bash
# 24/7 runner for the READ-ONLY L5 ops layer (issue #167 safe activation).
#
# Designed for an ISOLATED durable service (Railway research service, an AWS
# host, or any Docker/systemd box) built from the pinned research branch.
# It never touches main, never triggers the Railway trading deploy, uses no
# exchange credentials, and cannot place orders.
#
# Loop: ops collect + ops shadow every OPS_INTERVAL_SECONDS (default 1800),
# ops regression once per UTC day, heartbeat/failure artifacts as ops.py
# writes them.
#
# Durable state topology (redeploy-loop safe):
#   * CODE  comes from the branch/commit this container was built from
#     (report `git rev-parse` at activation; Railway pins the branch).
#   * DATA  (data/l5/ops: cursor state, raw rows, shadow, heartbeat,
#     failures) is persisted by committing to a SEPARATE data-only branch
#     (OPS_DATA_BRANCH, default l5-ops-data). Pushing data there does NOT
#     redeploy the service (Railway watches the code branch) and triggers
#     no workflow (repo workflows fire on issues/main-push only).
#   * On start the runner restores data/l5/ops from the data branch, so a
#     container replacement resumes from the last committed cursor.
#
# Env:
#   GIT_PUSH_TOKEN     fine-grained GitHub token, Contents:RW on THIS repo
#                      only. NOT an exchange credential. Optional: without
#                      it the runner still works but state is container-
#                      local only (heartbeat carries a durability warning).
#   OPS_REPO           owner/repo (default unbuiltworks/kalshi-arb-bot)
#   OPS_DATA_BRANCH    data branch (default l5-ops-data)
#   OPS_INTERVAL_SECONDS  cycle sleep (default 1800; floor 600)
#   OPS_ONESHOT=1      run one cycle and exit (smoke test / external cron)
#
# The token is never echoed: it lives in a git credential-store file and
# this script runs without xtrace.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="${OPS_REPO:-unbuiltworks/kalshi-arb-bot}"
REPO_URL="${OPS_REPO_URL:-https://github.com/${REPO}.git}"   # override for tests
DATA_BRANCH="${OPS_DATA_BRANCH:-l5-ops-data}"
INTERVAL="${OPS_INTERVAL_SECONDS:-1800}"
[ "$INTERVAL" -lt 600 ] && INTERVAL=600
DATA_DIR="data/l5/ops"
DATA_CLONE="${OPS_DATA_CLONE_DIR:-/tmp/l5-ops-data}"
LAST_REGRESSION_FILE="/tmp/l5_ops_last_regression_day"

log() { printf '%s l5-ops-runner: %s\n' "$(date -u +%FT%TZ)" "$*"; }

have_token() { [ -n "${GIT_PUSH_TOKEN:-}" ]; }

setup_git_auth() {
    # credential store keeps the token out of process args and logs
    git config --global credential.helper "store --file=/tmp/.l5ops-cred"
    printf 'https://x-access-token:%s@github.com\n' "$GIT_PUSH_TOKEN" \
        > /tmp/.l5ops-cred
    chmod 600 /tmp/.l5ops-cred
    git config --global user.name "l5-ops-runner"
    git config --global user.email "l5-ops-runner@users.noreply.github.com"
}

restore_data() {
    have_token || { log "no GIT_PUSH_TOKEN: container-local state only"; return 0; }
    if git clone --depth 1 --branch "$DATA_BRANCH" \
            "$REPO_URL" "$DATA_CLONE" 2>/dev/null; then
        if [ -d "$DATA_CLONE/$DATA_DIR" ]; then
            mkdir -p "$DATA_DIR"
            cp -a "$DATA_CLONE/$DATA_DIR/." "$DATA_DIR/"
            log "restored $(find "$DATA_DIR" -type f | wc -l) state files from $DATA_BRANCH"
        fi
    else
        log "data branch $DATA_BRANCH not found; starting fresh"
    fi
}

persist_data() {
    have_token || return 0
    [ -d "$DATA_CLONE/.git" ] || return 0
    mkdir -p "$DATA_CLONE/$DATA_DIR"
    cp -a "$DATA_DIR/." "$DATA_CLONE/$DATA_DIR/"
    (
        cd "$DATA_CLONE"
        git add -A
        git diff --cached --quiet && exit 0
        git commit -q -m "l5-ops: state snapshot $(date -u +%FT%TZ)"
        for i in 1 2 3; do
            git push -q origin "HEAD:$DATA_BRANCH" && exit 0
            git pull -q --rebase origin "$DATA_BRANCH" || true
            sleep $((2 ** i))
        done
        echo "persist push failed after retries" >&2
        exit 1
    ) || log "WARNING: state persist failed (kept locally; next cycle retries)"
}

cycle() {
    python3 -m src.polymarket_l5.ops collect || log "collect failed (failure artifact retained)"
    python3 -m src.polymarket_l5.ops shadow  || log "shadow failed (failure artifact retained)"
    local today
    today=$(date -u +%F)
    if [ "$(cat "$LAST_REGRESSION_FILE" 2>/dev/null || true)" != "$today" ]; then
        if python3 -m src.polymarket_l5.ops regression; then
            echo "$today" > "$LAST_REGRESSION_FILE"
        else
            log "regression failed (failure artifact retained)"
        fi
    fi
    persist_data
}

log "starting: repo=$REPO data_branch=$DATA_BRANCH interval=${INTERVAL}s oneshot=${OPS_ONESHOT:-0}"
log "code version: ${OPS_CODE_VERSION:-unknown-set-at-build}"
have_token && setup_git_auth
restore_data
if [ "${OPS_ONESHOT:-0}" = "1" ]; then
    cycle
    log "oneshot cycle complete"
    exit 0
fi
while true; do
    cycle
    sleep "$INTERVAL"
done
