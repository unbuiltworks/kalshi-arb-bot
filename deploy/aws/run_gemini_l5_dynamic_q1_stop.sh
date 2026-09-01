#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO=${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}
WORKTREE=${WORKTREE:-/opt/arb/gemini-l5-dynamic-q1-stop}
STATE_DIR=${STATE_DIR:-/opt/arb/state/gemini-l5-dynamic-q1}
BRANCH=feature/gemini-l5-dynamic-q1
PINNED_COMMIT=${PINNED_COMMIT:-}
APPROVAL_ID=${ARB_GEMINI_L5_DYNAMIC_STOP_APPROVAL_ID:-}
APPROVED=${ARB_GEMINI_L5_DYNAMIC_STOP_APPROVED:-NO}
CONTAINER=arb-gemini-l5-dynamic-q1

[ "$APPROVED" = YES ] || { echo missing_stop_approval >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo bad_pinned_commit >&2; exit 1; }
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || { echo bad_approval_id >&2; exit 1; }

ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
[ -f "$ENV_FILE" ] || ENV_FILE=$SOURCE_REPO/.env
[ -f "$ENV_FILE" ] || { echo missing_env >&2; exit 1; }
[ -d "$STATE_DIR" ] || { echo missing_dynamic_state_dir >&2; exit 1; }

SAFE=$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')
USED=$STATE_DIR/stop-${SAFE}.used
[ ! -e "$USED" ] || { echo stop_approval_already_used >&2; exit 1; }

# Build the exact audited stop logic before touching the running owner.
git -C "$SOURCE_REPO" fetch origin "$BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"; git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null
IMAGE=arb-gemini-l5-dynamic-stop:q1-${PINNED_COMMIT:0:12}
docker build -t "$IMAGE" "$WORKTREE" >/dev/null
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_l5_dynamic_exact_stop.py \
  tests/test_gemini_l5_pair_q1.py \
  tests/test_gemini_l5_dynamic_q1.py
echo L5_DYNAMIC_STOP_SAFETY_TESTS_OK

printf '%s\n' "approval=$SAFE commit=$PINNED_COMMIT consumed=$(date -u +%FT%TZ)" > "$USED"

# Freeze mutation first.  Do not remove the container yet: preserve logs/evidence
# until exact-ID reconciliation proves upgrade safety.
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  BEFORE=$(docker inspect -f '{{.State.Running}}' "$CONTAINER")
  echo "L5_DYNAMIC_STOP_BEGIN running_before=$BEFORE"
  if [ "$BEFORE" = true ]; then docker stop --time 5 "$CONTAINER" >/dev/null; fi
else
  echo "L5_DYNAMIC_STOP_BEGIN container_missing=true"
fi

set +e
OUT=$(docker run --rm --network host --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_L5_DYNAMIC_STATE_DIR=/state \
  -v "$STATE_DIR:/state:ro" \
  "$IMAGE" python -u -m src.gemini_l5_dynamic_exact_stop 2>&1)
RC=$?
set -e
printf '%s\n' "$OUT"
if [ "$RC" -ne 0 ]; then
  echo "L5_DYNAMIC_STOP_NOT_UPGRADE_SAFE rc=$RC container_left_stopped=true" >&2
  exit "$RC"
fi

grep -q '"upgrade_safe": true' <<<"$OUT"
# Only now remove the old stopped container.  The account was proven flat and no
# exact-cycle fill existed, so a separately approved new owner may start.
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
echo "L5_DYNAMIC_STOP_UPGRADE_SAFE container_removed=true"
