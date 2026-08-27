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

# Build the minimum read-only runtime env without printing secret values. Existing
# local env/key files are consulted first. Missing values are prompted with terminal
# echo disabled. The PEM reader accepts either a normal multi-line PEM paste or a
# one-line value containing literal backslash-n escapes.
python3 - "$ENV_FILE" "$REPO_DIR/.env.gemini-live" "$REPO_DIR/.env" "$REPO_DIR" <<'PY'
import getpass
import os
import re
import sys
import termios
import textwrap
from pathlib import Path

out_path = Path(sys.argv[1])
local_sources = [Path(p) for p in sys.argv[2:4]]
repo_dir = Path(sys.argv[4])
scalar_sources = [out_path, *local_sources]
required_scalar = [
    "KALSHI_API_KEY_ID",
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


def normalize_pem(value: str) -> str:
    value = value.strip()
    if value.startswith("KALSHI_PRIVATE_KEY_PEM="):
        value = value.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    value = value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    match = re.search(
        r"-----BEGIN ([A-Z0-9 ]+?)-----(.*?)-----END \1-----",
        value,
        flags=re.DOTALL,
    )
    if match:
        kind = match.group(1)
        body = re.sub(r"\s+", "", match.group(2))
        if body:
            wrapped = "\n".join(textwrap.wrap(body, 64))
            value = f"-----BEGIN {kind}-----\n{wrapped}\n-----END {kind}-----"
    return value.strip() + "\n"


def prompt_pem() -> str:
    print("Enter KALSHI_PRIVATE_KEY_PEM (multi-line PEM or one-line backslash-n value).")
    print("Input is hidden. For a normal PEM paste, finish after the -----END ...----- line.")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    lines: list[str] = []
    try:
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        while True:
            raw = sys.stdin.readline()
            if raw == "":
                raise SystemExit("stdin closed while reading KALSHI_PRIVATE_KEY_PEM")
            line = raw.rstrip("\r\n")
            lines.append(line)
            joined = "\n".join(lines)
            if re.search(r"-----END [A-Z0-9 ]+?-----", line):
                break
            if len(lines) == 1 and "-----BEGIN " in joined and "-----END " in joined:
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()
    return normalize_pem("\n".join(lines))


parsed_scalar_sources = [(path, parse_env(path)) for path in scalar_sources]
parsed_local_sources = [(path, parse_env(path)) for path in local_sources]
values: dict[str, str] = {}
used_sources: list[str] = []
for source, parsed in parsed_scalar_sources:
    found_here = False
    for key in required_scalar:
        if not values.get(key) and parsed.get(key):
            values[key] = parsed[key]
            found_here = True
    if found_here:
        used_sources.append(str(source))

# Deliberately do NOT reuse KALSHI_PRIVATE_KEY_PEM from the existing AWS env file:
# the previous failed attempt may contain malformed framing. Only trusted local envs
# or actual key files are eligible for automatic PEM reuse.
pem: str | None = None
pem_source: str | None = None
for source, parsed in parsed_local_sources:
    raw = parsed.get("KALSHI_PRIVATE_KEY_PEM", "").strip()
    if raw:
        pem = normalize_pem(raw)
        pem_source = str(source) + ":KALSHI_PRIVATE_KEY_PEM"
        break

candidate_paths: list[Path] = []
for source, parsed in parsed_local_sources:
    raw_path = parsed.get("KALSHI_PRIVATE_KEY_PATH", "").strip()
    if raw_path:
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = repo_dir / p
        candidate_paths.append(p)
candidate_paths.extend([
    repo_dir / "private_key.pem",
    repo_dir / "kalshi_private_key.pem",
    Path("/home/ssm-user/private_key.pem"),
    Path("/home/ssm-user/.kalshi/private_key.pem"),
])
if pem is None:
    seen: set[str] = set()
    for path in candidate_paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                raw = path.read_text(errors="strict")
                if "-----BEGIN " in raw and "PRIVATE KEY-----" in raw:
                    pem = normalize_pem(raw)
                    pem_source = str(path)
                    break
        except (OSError, UnicodeError):
            continue

for key in required_scalar:
    if values.get(key):
        continue
    while True:
        value = getpass.getpass(f"Enter {key}: ").strip()
        if value:
            values[key] = value
            break
        print(f"{key} cannot be empty", file=sys.stderr)

if pem is None:
    pem = prompt_pem()
    pem_source = "interactive_hidden_input"

if "-----BEGIN " not in pem or "PRIVATE KEY-----" not in pem or "-----END " not in pem:
    raise SystemExit("KALSHI_PRIVATE_KEY_PEM does not contain complete PEM framing")


def one_line(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")

lines = [
    "BOT_MODE=paper",
    "KALSHI_ENV=prod",
    "PYTHONUNBUFFERED=1",
    "LOG_LEVEL=INFO",
    f"KALSHI_API_KEY_ID={values['KALSHI_API_KEY_ID']}",
    f"KALSHI_PRIVATE_KEY_PEM={one_line(pem)}",
    f"POLYMARKET_KEY_ID={values['POLYMARKET_KEY_ID']}",
    f"POLYMARKET_SECRET_KEY={values['POLYMARKET_SECRET_KEY']}",
]

tmp = out_path.with_suffix(out_path.suffix + ".tmp")
tmp.write_text("\n".join(lines) + "\n")
os.chmod(tmp, 0o640)
os.replace(tmp, out_path)
print("SECURE_ENV_WRITTEN")
print("REUSED_LOCAL_SOURCES=" + (",".join(used_sources) if used_sources else "none"))
print("KALSHI_PEM_SOURCE=" + (pem_source or "unknown"))
print("REQUIRED_VALUES_PRESENT=4/4")
PY

chown root:arb "$ENV_FILE"
chmod 0640 "$ENV_FILE"

if ! docker run --rm \
  --env-file "$ENV_FILE" \
  -e BOT_MODE=paper \
  "$IMAGE" \
  python -c 'from src.auth import load_private_key; load_private_key(); print("KALSHI_PRIVATE_KEY_PARSE_OK")'; then
  echo "ERROR: Kalshi private key still failed to parse. The AWS shadow was NOT started." >&2
  exit 1
fi

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
docker logs --tail 40 arb-sports-maker-shadow-aws 2>&1 || true
