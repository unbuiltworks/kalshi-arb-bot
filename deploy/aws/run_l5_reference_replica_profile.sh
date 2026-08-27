#!/usr/bin/env bash
set -euo pipefail

# READ-ONLY profiler for the completed l5 full-history SQLite spool.
# No API fetch, no credentials, no order entry, no running-service changes.
# The authoritative host spool is mounted read-only. A disposable DB/WAL/SHM
# copy is made inside the container so SQLite can coordinate WAL state safely.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/l5-reference-replica-profile}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/arb/l5-full-history-output}"
FEATURE_BRANCH="ev-wallet-shadow"
PINNED_COMMIT="4e2f50116a1b5c1b359103293b321feec61cb39f"
IMAGE="arb-l5-reference-replica-profile:4e2f501"
OUT_JSON="$OUTPUT_DIR/l5_reference_replica_profile.json"

for cmd in git docker find; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing: $SOURCE_REPO" >&2; exit 1; }
mkdir -p "$OUTPUT_DIR"

# Reuse the already-completed spool; never refetch history.
DB="${DB:-}"
if [ -z "$DB" ]; then
  DB="$(find /opt/arb/l5-full-history-output /opt/arb/l5-full-history-backtest /opt/arb -maxdepth 3 -type f \( -name '*spool*.sqlite3' -o -name '*history*.sqlite3' \) 2>/dev/null | head -n 1 || true)"
fi
[ -n "$DB" ] && [ -s "$DB" ] || { echo "completed l5 SQLite spool not found" >&2; exit 1; }
DB_DIR="$(dirname "$DB")"
DB_NAME="$(basename "$DB")"
echo "L5_REPLICA_PROFILE_DB_FOUND path=$DB bytes=$(stat -c '%s' "$DB")"
for sidecar in "$DB-wal" "$DB-shm"; do
  if [ -f "$sidecar" ]; then
    echo "L5_REPLICA_PROFILE_SIDECAR path=$sidecar bytes=$(stat -c '%s' "$sidecar")"
  fi
done

git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$FEATURE_BRANCH"
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT"
actual="$(git -C "$WORKTREE" rev-parse HEAD)"
test "$actual" = "$PINNED_COMMIT"
echo "L5_REPLICA_PROFILE_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Keep authoritative host files read-only. Copy the SQLite database and any WAL
# sidecars to writable /tmp inside the disposable container before opening it.
docker run --rm --network none \
  -e BOT_MODE=paper \
  -e PYTHONUNBUFFERED=1 \
  -e INPUT_DB_NAME="$DB_NAME" \
  -v "$DB_DIR:/input:ro" \
  -v "$OUTPUT_DIR:/out" \
  "$IMAGE" \
  sh -lc '
    set -eu
    echo "L5_REPLICA_PROFILE_CONTAINER_INPUT"
    ls -l "/input/$INPUT_DB_NAME" "/input/$INPUT_DB_NAME-wal" "/input/$INPUT_DB_NAME-shm" 2>/dev/null || true
    cp "/input/$INPUT_DB_NAME" /tmp/l5_spool.sqlite3
    [ ! -f "/input/$INPUT_DB_NAME-wal" ] || cp "/input/$INPUT_DB_NAME-wal" /tmp/l5_spool.sqlite3-wal
    [ ! -f "/input/$INPUT_DB_NAME-shm" ] || cp "/input/$INPUT_DB_NAME-shm" /tmp/l5_spool.sqlite3-shm
    chmod u+rw /tmp/l5_spool.sqlite3 /tmp/l5_spool.sqlite3-wal /tmp/l5_spool.sqlite3-shm 2>/dev/null || true
    python - <<"PY"
import sqlite3
p="/tmp/l5_spool.sqlite3"
db=sqlite3.connect(p)
print("L5_REPLICA_PROFILE_SQLITE_SMOKE", db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], flush=True)
print("L5_REPLICA_PROFILE_TRADE_ROWS", db.execute("SELECT COUNT(*) FROM activity WHERE kind=\"TRADE\"").fetchone()[0], flush=True)
db.close()
PY
    python -u -m src.polymarket_l5_replica_profile \
      --db /tmp/l5_spool.sqlite3 \
      --output /out/l5_reference_replica_profile.json
  '

[ -s "$OUT_JSON" ] || { echo "replica profile output missing: $OUT_JSON" >&2; exit 1; }

echo "=== L5 REFERENCE REPLICA PROFILE SUMMARY ==="
python3 - "$OUT_JSON" <<'PY'
import json, sys
p=json.load(open(sys.argv[1]))
for k in ("overall","by_horizon"):
    print(json.dumps({k:p.get(k)}, sort_keys=True))
print(json.dumps({"by_stream":p.get("by_stream")}, sort_keys=True))
print("shadow_only=true order_entry=false realized_pnl=0")
PY

echo "L5_REPLICA_PROFILE_COMPLETE output=$OUT_JSON commit=$PINNED_COMMIT"
