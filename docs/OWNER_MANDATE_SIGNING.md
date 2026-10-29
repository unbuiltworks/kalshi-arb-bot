# Owner mandate signing — offline procedure

**I never generate, see, hold or transmit your private key.** It is created by
you, on your machine, and never enters this repository, Railway, Claude Code,
or any environment the executor can read. The executor holds only the public
half, compiled into the image.

## Step 1 — generate the keypair (you, offline, once)

```bash
mkdir -p ~/.l5 && chmod 700 ~/.l5
python3 - <<'EOF'
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization as S
import os
p = ed25519.Ed25519PrivateKey.generate()
seed = p.private_bytes(encoding=S.Encoding.Raw, format=S.PrivateFormat.Raw,
                       encryption_algorithm=S.NoEncryption())
fd = os.open(os.path.expanduser("~/.l5/mandate_ed25519.key"),
             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, seed); os.close(fd)
print("PUBLIC KEY (give this to me):",
      p.public_key().public_bytes(encoding=S.Encoding.Raw,
                                  format=S.PublicFormat.Raw).hex())
EOF
```

Back up the key file somewhere offline. **If it is ever pasted into Railway, a
chat, a terminal I can read, or committed — treat it as burned**: delete the
`MANDATE_PUBKEYS` entry, generate a new pair, and bump `seq` past the highest
value the old key ever signed.

## Step 2 — give me the public key

Paste the 64-hex public key. I add it to `MANDATE_PUBKEYS` in
`src/l5_mandate_signature.py` as a reviewed commit, with a `key_id` such as
`owner-2026-10`. Until an entry exists, every mandate is refused with
`MANDATE_NO_TRUSTED_KEY` — the shipped state.

Never reuse a `key_id` for a different key: `seq` monotonicity is tracked per
`key_id`.

## Step 3 — write the payload

```json
{
  "schema": "l5.mandate.v1",
  "kind": "standing",
  "mandate_id": "l5-standing-001",
  "seq": 1,
  "venue": "PMUS",
  "strategy": "l5-two-sided-btc15m",
  "environment": "production",
  "service_role": "l5-pmus-live",
  "series": "cpc-btc-updown-15m-",
  "not_before": "2026-11-01T00:00:00+00:00",
  "not_after":  "2026-11-29T00:00:00+00:00",
  "allowed_actions": ["OPEN", "COMPLETE", "CANCEL"],
  "execution_mode": "MAKER_ONLY",
  "max_windows_per_day": 4,
  "max_submissions_per_day": 12,
  "capital": {
    "max_cash_deployed_usd": "1.65",
    "max_total_exposure_usd": "2.00",
    "campaign_budget_usd": "29.30"
  },
  "loss_limits": {
    "daily_realized_loss_halt_usd": "5",
    "max_cumulative_loss_usd": "20"
  },
  "residual_cap_contracts": 3,
  "promotion": {
    "min_settled_windows": 300,
    "min_expectancy_usd": "0.05",
    "oos_evidence_id": "btc15m-oos-20261023",
    "requires_bounded_live_validation": true
  }
}
```

Rules the verifier enforces, each a refusal:

- **Every field above is required and no extra field is allowed.** A typo like
  `max_windows_per_dey` is `MANDATE_UNKNOWN_FIELD`, never silently ignored.
- **Money is a string.** `1.65` as a JSON float is `MANDATE_MONEY_INVALID`,
  because 0.1 is not 0.1 in binary and a limit that differs from the one you
  signed is not your limit.
- **Timestamps need an explicit offset.** `"2026-11-01T00:00:00"` is refused
  rather than assumed UTC.
- **Duration ≤ 30 days** (`MAX_MANDATE_S`).
- **`execution_mode` must be `MAKER_ONLY`** and no action name expresses
  crossing, so no signed mandate can authorize a taker order.
- **`requires_bounded_live_validation` must be `true`.** There is no field
  that can make shadow expectancy promote to live on its own.
- **`seq` strictly increases.** To replace a mandate, raise `seq`; the old one
  then refuses as `MANDATE_REPLAYED_OR_ROLLED_BACK`, which is also what stops
  a rollback to an older, looser document.

## Step 4 — sign

```bash
python3 tools/owner_sign_mandate.py \
  --key ~/.l5/mandate_ed25519.key \
  --payload my_mandate.json \
  --key-id owner-2026-10 \
  --out signed_mandate.json
```

The tool refuses a key inside the repo tree or a key file that is group- or
world-readable, and it dry-runs the real verifier so a payload the executor
would reject is caught on your machine.

## Step 5 — install (phase 5 only, not yet)

Paste `signed_mandate.json` into **`L5_MANDATE_DOC`** on `l5-pmus-live`.

Installing it changes nothing today: phase 1 is reporting-only, nothing in the
arming path consults it, and production stays shadow-locked. You can install
it now purely to see `mandate_verdict` turn to `OK` in the heartbeat.

## Revoking

1. **Halt latch** on the watchdog — fastest, no redeploy, executor cannot clear.
2. **Revocation list** — add `"l5-standing-001"` or `"l5-standing-001#4"`.
   (Served from the watchdog volume; lands in phase 2.)
3. **Raise `seq`** with a new mandate, or clear `L5_MANDATE_DOC`.
