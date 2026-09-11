#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo -n bash "$0" "$@"
fi

REPO_DIR="/home/ssm-user/kalshi-arb-bot"
ENV_DIR="/etc/arb"
ENV_FILE="$ENV_DIR/sports-maker-shadow.env"
WORK_ROOT="/opt/arb"
ARB_GROUP="arb"

if ! id ssm-user >/dev/null 2>&1; then
  echo "ERROR: expected ssm-user account is missing" >&2
  exit 1
fi

# Install Docker without changing strategy code or venue state.
if ! command -v docker >/dev/null 2>&1; then
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
  fi

  case "${ID:-}:${VERSION_ID:-}" in
    amzn:2023*)
      yum install -y docker
      ;;
    amzn:2*)
      if command -v amazon-linux-extras >/dev/null 2>&1; then
        amazon-linux-extras install docker -y
      else
        yum install -y docker
      fi
      ;;
    ubuntu:22.04*|ubuntu:24.04*|ubuntu:26.04*)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      # Remove conflicting distro packages if present. Missing packages are harmless.
      apt-get remove -y docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc 2>/dev/null || true
      apt-get install -y ca-certificates curl
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-${VERSION_CODENAME}}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    *)
      echo "ERROR: unsupported OS for automatic Docker bootstrap: ${PRETTY_NAME:-unknown}" >&2
      exit 1
      ;;
  esac
fi

systemctl enable --now docker

# Dedicated group owns ARB runtime files. ssm-user also needs docker access.
getent group "$ARB_GROUP" >/dev/null 2>&1 || groupadd --system "$ARB_GROUP"
usermod -aG docker,"$ARB_GROUP" ssm-user

install -d -m 0750 -o root -g "$ARB_GROUP" "$ENV_DIR"
install -d -m 0770 -o ssm-user -g "$ARB_GROUP" "$WORK_ROOT"

cat > "$ENV_DIR/sports-maker-shadow.env.example" <<'EOF'
# Variable NAMES only. Do not store plaintext secrets in git or SSM Run Command history.
BOT_MODE=paper
KALSHI_ENV=prod
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PEM=
POLYMARKET_KEY_ID=
POLYMARKET_SECRET_KEY=
ARB_LEDGER_URL=
LEDGER_INGEST_TOKEN=
SPORTS_POLY_RELAY_LIVE_Q1=
PYTHONUNBUFFERED=1
EOF
chown root:"$ARB_GROUP" "$ENV_DIR/sports-maker-shadow.env.example"
chmod 0640 "$ENV_DIR/sports-maker-shadow.env.example"

# Normalize an existing secure env file without reading or printing it.
if [[ -f "$ENV_FILE" ]]; then
  chown root:"$ARB_GROUP" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ERROR: expected repo is missing at $REPO_DIR" >&2
  exit 1
fi

# Run git as the repository owner instead of marking the repo safe for root.
sudo -u ssm-user -H git -C "$REPO_DIR" status --short
sudo -u ssm-user -H git -C "$REPO_DIR" fetch origin aws-migration-foundation feature/sports-kp-live-executor-impl

# New sudo processes pick up supplementary groups from the account database.
docker --version
sudo -u ssm-user -H id
if ! sudo -u ssm-user -H docker ps >/dev/null 2>&1; then
  echo "ERROR: ssm-user cannot access Docker after group update" >&2
  exit 1
fi

echo "BOOTSTRAP_OK"
if [[ -f "$ENV_FILE" ]]; then
  echo "ENV_READY"
else
  echo "ENV_MISSING"
fi
