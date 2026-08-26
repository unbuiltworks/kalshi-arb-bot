#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ssm-user/kalshi-arb-bot"
BRANCH="aws-migration-foundation"
ENV_DIR="/etc/arb"
ENV_FILE="$ENV_DIR/sports-maker-shadow.env"
IMAGE="arb-sports-maker-shadow:aws-parity"
RUNNER="/tmp/arb-run-sports-maker-shadow.sh"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo -n bash "$0" "$@"
fi

if [[ ! -t 0 ]]; then
  echo "ERROR: this helper must be run in an interactive Session Manager shell so secrets are entered without echo." >&2
  exit 1
fi

if ! id ssm-user >/dev/null 2>&1; then
  echo "ERROR: expected ssm-user account is missing" >&2
  exit 1
fi
if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ERROR: expected repo is missing at $REPO_DIR" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "ERROR: prepared image $IMAGE is missing; run deploy/aws/prepare_hot_host.sh first" >&2
  exit 1
fi

install -d -m 0750 -o root -g arb "$ENV_DIR"

# Build the minimum read-only runtime env without printing any secret values.
# Existing local env files are consulted first so the EC2 host can reuse credentials
# already present there. Missing values are prompted with terminal echo disabled.
python3 - "$ENV_FILE" "$REPO_DIR/.env.gemini-live" "$REPO_DIR/.env" <<'PY'
import getpass
import os
import re
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
sources = [Path(p) for p in sys.argv[2:]]
required = [
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PEM",
    "POLYMARKET_KEY_ID",
    "POLYMARKET_SECRET_KEY",
]


def parse_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key] = value
    return data

values: dict[str, str] = {}
used_sources: list[str] = []
for source in sources:
    parsed = parse_env(source)
    found_here = False
    for key in required:
        if not values.get(key) and parsed.get(key):
            values[key] = parsed[key]
            found_here = True
    if found_here:
        used_sources.append(str(source))

for key in required:
    if values.get(key):
        continue
    label = key
    if key == "KALSHI_PRIVATE_KEY_PEM":
        label += " (paste the one-line backslash-n escaped value)"
    while True:
        value = getpass.getpass(f"Enter {label}: ").strip()
        if value:
            values[key] = value
            break
        print(f"{key} cannot be empty", file=sys.stderr)

# Docker --env-file requires one physical line per variable. Preserve literal
# backslash-n escapes and convert any actual newline into the same representation.
def one_line(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")

lines = [
    "BOT_MODE=paper",
    "KALSHI_ENV=prod",
    "PYTHONUNBUFFERED=1",
    "LOG_LEVEL=INFO",
]
for key in required:
    lines.append(f"{key}={one_line(values[key])}")

# Atomic write with restrictive permissions. Never print file contents.
tmp = out_path.with_suffix(out_path.suffix + ".tmp")
tmp.write_text("\n".join(lines) + "\n")
os.chmod(tmp, 0o640)
os.replace(tmp, out_path)
print("SECURE_ENV_WRITTEN")
print("REUSED_LOCAL_SOURCES=" + (",".join(used_sources) if used_sources else "none"))
print("REQUIRED_VALUES_PRESENT=4/4")
PY

chown root:arb "$ENV_FILE"
chmod 0640 "$ENV_FILE"

# Validate that the Kalshi PEM can actually be parsed inside the exact parity image.
docker run --rm \
  --env-file "$ENV_FILE" \
  -e BOT_MODE=paper \
  "$IMAGE" \
  python -c 'from src.auth import load_private_key; load_private_key(); print("KALSHI_PRIVATE_KEY_PARSE_OK")'

# Pull the latest migration runner itself, then launch the parity shadow as ssm-user.
sudo -u ssm-user -H git -C "$REPO_DIR" fetch origin "$BRANCH"
sudo -u ssm-user -H bash -lc \
  "git -C '$REPO_DIR' show origin/$BRANCH:deploy/aws/run_sports_maker_shadow.sh > '$RUNNER'"
chown ssm-user:ssm-user "$RUNNER"
chmod 0755 "$RUNNER"

sudo -u ssm-user -H "$RUNNER"

sleep 5
if ! docker ps --filter name=arb-sports-maker-shadow-aws --filter status=running --format '{{.Names}}' | grep -qx 'arb-sports-maker-shadow-aws'; then
  echo "ERROR: AWS sports maker shadow did not remain running" >&2
  docker logs --tail 80 arb-sports-maker-shadow-aws 2>&1 || true
  exit 1
fi

echo "AWS_SPORTS_MAKER_SHADOW_RUNNING"
echo "READ_ONLY=TRUE"
echo "ORDER_ENTRY=FALSE"
# Show only application logs. The strategy does not log credentials.
docker logs --tail 40 arb-sports-maker-shadow-aws 2>&1 || true
