#!/usr/bin/env bash
# ONE COMMAND for the owner. Run it on YOUR machine, never in a container
# the executor can read.
#
#   bash tools/owner_setup.sh
#
# WHAT IT DOES
#   1. creates ~/.l5 (owner-only) and, if absent, your private Ed25519 key
#   2. installs the one Python package signing needs, only if missing
#   3. signs config/mandate_payload_pilot.json with your key
#   4. writes a fresh campaign approval document and picks the four pilot
#      windows, so nothing is stale by the time you paste it
#
# WHAT IT NEVER DOES
#   It never prints, copies, uploads or transmits your private key, and it
#   never overwrites an existing one. An Ed25519 private key IS 32 random
#   bytes, so generating it needs no cryptography library at all -- only
#   signing does. If you run this twice you get the SAME public key.
set -euo pipefail

if [ -z "${HOME:-}" ]; then
  echo "HOME is not set; refusing to guess where your key should live." >&2
  exit 2
fi

KEY="$HOME/.l5/mandate_ed25519.key"
OUT="$HOME/.l5"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
KEY_ID="${KEY_ID:-owner-2026-11}"

# THE KEY MUST NEVER LIVE IN THE REPOSITORY. If HOME somehow sits inside the
# checkout, stop rather than create a key that a later `git add` could
# commit. The signing tool refuses this too; two checks, because the cost of
# being wrong once is the whole control.
case "$OUT/" in
  "$REPO"/*) echo "refusing: $OUT is inside the repository ($REPO)." >&2
             exit 2 ;;
esac

mkdir -p "$OUT"; chmod 700 "$OUT"

# ---- 0. a usable python3, named clearly if absent ----------------------
# On a Mac that has never had the Command Line Tools, /usr/bin/python3 is a
# STUB: it opens a GUI dialog and exits non-zero. Saying so beats a cryptic
# failure three steps later.
if ! python3 -c "import sys" >/dev/null 2>&1; then
  cat >&2 <<'MSG'
python3 is not usable on this Mac yet.

Run this once, click Install in the dialog, wait for it to finish, then run
this script again:

    xcode-select --install

That installs Apple's Command Line Tools. It is not Xcode, it needs no
Apple developer account, and it takes a couple of minutes.
MSG
  exit 2
fi
PY_BIN="python3"

# ---- 1. the key. Stdlib only, never overwritten. ------------------------
if [ -f "$KEY" ]; then
  echo "[1/4] existing key kept: $KEY  (not overwritten)"
else
  python3 - "$KEY" <<'PY'
import os, sys
path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, os.urandom(32)); os.close(fd)
PY
  chmod 600 "$KEY"
  echo "[1/4] private key created: $KEY"
  echo "      BACK THIS FILE UP OFFLINE. It is the only copy."
fi

# ---- 2. the one dependency, only if missing -----------------------------
# Three routes, because the right one depends on which python3 this Mac has:
# Apple's system Python takes --user; a Homebrew Python refuses it under
# PEP 668 as "externally-managed"; a private venv works on all of them and
# needs no flags. Prebuilt wheels exist for macOS, so no compiler is needed
# on any route.
if "$PY_BIN" -c "import cryptography" >/dev/null 2>&1; then
  echo "[2/4] cryptography already available"
elif "$PY_BIN" -m pip install --user --quiet cryptography >/dev/null 2>&1 \
     && "$PY_BIN" -c "import cryptography" >/dev/null 2>&1; then
  echo "[2/4] cryptography installed for your user"
else
  echo "[2/4] using a private virtual environment instead..."
  "$PY_BIN" -m venv "$OUT/venv" >/dev/null 2>&1 || true
  if [ -x "$OUT/venv/bin/python" ]; then
    "$OUT/venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
    "$OUT/venv/bin/python" -m pip install --quiet cryptography
    PY_BIN="$OUT/venv/bin/python"
    echo "      using $PY_BIN"
  fi
  if ! "$PY_BIN" -c "import cryptography" >/dev/null 2>&1; then
    echo >&2
    echo "Could not install the 'cryptography' package." >&2
    echo "Send Claude the error text above rather than working around it." >&2
    exit 2
  fi
fi

# ---- 3. sign the prepared payload --------------------------------------
echo "[3/4] signing config/mandate_payload_pilot.json ..."
"$PY_BIN" "$REPO/tools/owner_sign_mandate.py" \
  --key "$KEY" \
  --payload "$REPO/config/mandate_payload_pilot.json" \
  --key-id "$KEY_ID" \
  --out "$OUT/signed_mandate.json"

# ---- 4. a FRESH campaign approval + the four pilot windows -------------
# Issued now, because the gate treats an approval older than its 4-hour
# campaign ceiling as `approval_stale` -- which is the exact blocker the
# deployed preflight is reporting today.
"$PY_BIN" - "$OUT" <<'PY'
import datetime as dt, json, os, secrets, sys
out = sys.argv[1]
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
# first control window: the next :00/:15/:30/:45 at least 20 minutes away
t = now + dt.timedelta(minutes=20)
t = t.replace(second=0, microsecond=0)
t += dt.timedelta(minutes=(-t.minute) % 15)
w = [t, t + dt.timedelta(minutes=15),
     t + dt.timedelta(minutes=45), t + dt.timedelta(minutes=60)]
skipped = t + dt.timedelta(minutes=30)
expires = w[3] + dt.timedelta(minutes=20)
doc = {"approved": True,
       "nonce": secrets.token_hex(12),
       "series": "cpc-btc-updown-15m-",
       "issued_utc": now.isoformat(),
       "starts_utc": w[0].isoformat(),
       "expires_utc": expires.isoformat(),
       "max_windows": 4,
       "max_total_exposure_usd": "20",
       "max_daily_loss_usd": "5"}
p = os.path.join(out, "campaign_approval.json")
open(p, "w").write(json.dumps(doc, separators=(",", ":"), sort_keys=True))
z = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
print("[4/4] campaign approval written: %s" % p)
print()
print("  PILOT WINDOWS (UTC)")
print("    control  : %s  and  %s" % (z(w[0]), z(w[1])))
print("    SKIPPED  : %s   (settlement + retirement + the one restart)" % z(skipped))
print("    at-touch : %s  and  %s" % (z(w[2]), z(w[3])))
print()
print("  L5_LIVE_AUTH_NOT_AFTER  (paste this value)")
print("    %s" % z(expires))
PY

echo
echo "=================  WHAT TO DO WITH THE OUTPUT  ================="
echo "SEND TO CLAUDE (neither is secret, neither can be forged):"
echo "  a) the PUBLIC key printed in step 3"
echo "  b) the contents of $OUT/signed_mandate.json"
echo
echo "PASTE YOURSELF into Railway -> l5-pmus-live -> Variables:"
echo "  L5_CAMPAIGN_APPROVAL   = contents of $OUT/campaign_approval.json"
echo "  L5_LIVE_AUTH_NOT_AFTER = the value printed in step 4"
echo
echo "Those two ARE the authorization. Claude cannot set them for you."
echo "Your private key stays in $KEY and goes nowhere."
echo "==============================================================="
