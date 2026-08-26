#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo -n bash "$0" "$@"
fi

REPO_DIR="/home/ssm-user/kalshi-arb-bot"
ENV_DIR="/etc/arb"
ENV_FILE="$ENV_DIR/sports-maker-shadow.env"

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
      # Docker's official apt repository supports Ubuntu 22.04, 24.04, and 26.04.
      apt-get update
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
usermod -aG docker ssm-user

install -d -m 0750 -o root -g root "$ENV_DIR"

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
chmod 0600 "$ENV_DIR/sports-maker-shadow.env.example"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ERROR: expected repo is missing at $REPO_DIR" >&2
  exit 1
fi

# Run git as the repository owner instead of marking the repo safe for root.
sudo -u ssm-user -H git -C "$REPO_DIR" status --short
sudo -u ssm-user -H git -C "$REPO_DIR" fetch origin aws-migration-foundation feature/sports-kp-live-executor-impl

# Verify Docker and the newly-added group membership. New sudo processes pick up
# supplementary groups from the account database.
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
