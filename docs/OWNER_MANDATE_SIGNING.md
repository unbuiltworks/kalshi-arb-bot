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

## Step 5 — install

Paste `signed_mandate.json` into **`L5_MANDATE_DOC`** on `l5-pmus-live`.

**This section used to say "phase 5 only, not yet" and that installing it
"changes nothing today" because "nothing in the arming path consults it".
That is no longer true and the correction matters:** `authorize_arming`
consults the mandate at step 1b, *first*, before the halt and preflight
steps. The gate is keyed on the registry, not on a phase flag:

- **`MANDATE_PUBKEYS` empty (today).** The mandate is reporting-only. The
  heartbeat shows `mandate_verdict`, `mandate_enforcing: false` and
  `promotion_gate_binds: false`. You may install a document purely to watch
  the verdict, and it authorizes nothing.
- **`MANDATE_PUBKEYS` non-empty.** A valid signed mandate becomes
  **required** for live arming. With a key registered and no document,
  arming refuses `MANDATE_ABSENT`; with a forged or unsigned one it refuses
  `MANDATE_*`. No Railway edit substitutes for the signature.

So the order is: install and watch the verdict *first*, confirm it reads OK,
and only then commit the public key — because committing the key is what
makes every subsequent arming depend on the document being right.

### What the signature does and does not bind

Worth being exact, because the payload carries more numbers than the gate
compares. At step 1b the signed scope is checked against the running limits
on **exactly two** quantities:

| signed field | running limit | refusal if running is wider |
|---|---|---|
| `capital.max_total_exposure_usd` | `L5_MAX_TOTAL_EXPOSURE_USD` | `MANDATE_LIMITS_WIDER_THAN_SIGNED` |
| `loss_limits.daily_realized_loss_halt_usd` | `L5_DAILY_REALIZED_LOSS_HALT_USD` | `MANDATE_LIMITS_WIDER_THAN_SIGNED` |

`execution_mode` must be `MAKER_ONLY` (re-asserted here, so a signed mandate
can never be read as authorizing a crossing order) and
`requires_bounded_live_validation` must be `true`.

Every other field — `max_cash_deployed_usd`, `campaign_budget_usd`,
`max_cumulative_loss_usd`, `residual_cap_contracts`,
`max_windows_per_day`, `max_submissions_per_day` — is **required and
schema-checked but not compared against a running limit at this gate**. Those
are bounded elsewhere (code constants, the campaign approval's intersection,
and the per-market residual gate). Do not read the signature as enforcing
them.

### It is an ADDITIONAL condition, not a replacement

With a key registered, live arming needs **both**:

1. a valid signed mandate (step 1b), **and**
2. either a valid campaign approval document or the `PMUS_Q1_*` handshake
   (step 2).

The signature exists because the campaign approval is served from the
`L5_CAMPAIGN_APPROVAL` *environment variable*: anything holding a Railway
token can write it, including the agent. The signature makes writing the
bytes insufficient. That is the whole and only thing it adds.

### Two prerequisites, both already satisfied on `l5-pmus-live`

A signed mandate is refused unless both exist, so these are not optional:

- **`L5_HALT_URL`** — without the latch there is no immediate withdrawal
  path for a standing mandate, and it refuses
  `MANDATE_REVOCATION_UNAVAILABLE`.
- **`L5_MANDATE_STORE_URL`** — the watchdog publishes the replay floor and
  the revocation list. They are read from the watchdog and not from our own
  disk on purpose: a floor this process could write is a floor it could roll
  back, which would let an older, looser mandate be replayed. Absent, stale,
  malformed or unreachable all refuse.

### A filled-in payload for the current four-window pilot

The example in Step 3 carries the older Stage-A numbers. For the pilot as
currently configured — exposure ceiling $20, daily loss halt $5, four
windows, 1-contract opening clip — the minimum correct payload is:

```json
{
  "schema": "l5.mandate.v1",
  "kind": "standing",
  "mandate_id": "l5-standing-002",
  "seq": 2,
  "venue": "PMUS",
  "strategy": "l5-two-sided-btc15m",
  "environment": "production",
  "service_role": "l5-pmus-live",
  "series": "cpc-btc-updown-15m-",
  "not_before": "2026-11-03T00:00:00+00:00",
  "not_after":  "2026-11-10T00:00:00+00:00",
  "allowed_actions": ["OPEN", "COMPLETE", "CANCEL"],
  "execution_mode": "MAKER_ONLY",
  "max_windows_per_day": 4,
  "max_submissions_per_day": 12,
  "capital": {
    "max_cash_deployed_usd": "12",
    "max_total_exposure_usd": "20",
    "campaign_budget_usd": "20"
  },
  "loss_limits": {
    "daily_realized_loss_halt_usd": "5",
    "max_cumulative_loss_usd": "20"
  },
  "residual_cap_contracts": 1,
  "promotion": {
    "min_settled_windows": 300,
    "min_expectancy_usd": "0.05",
    "oos_evidence_id": "btc15m-oos-20261023",
    "requires_bounded_live_validation": true
  }
}
```

Notes on those values:

- `max_total_exposure_usd` **must be at least the running
  `L5_MAX_TOTAL_EXPOSURE_USD` (20)** and `daily_realized_loss_halt_usd` at
  least the running halt (5), or arming refuses
  `MANDATE_LIMITS_WIDER_THAN_SIGNED`. Signing *tighter* numbers than the
  deployed ones does not tighten them — it refuses arming altogether. To
  actually run tighter, lower the variables and sign to match.
- `seq` is 2 because `l5-standing-001` appears in the Step 3 example; use a
  value strictly above anything this `key_id` has ever signed.
- `not_after` is one week, well inside the 30-day `MAX_MANDATE_S`.
- `residual_cap_contracts: 1` records the pilot's intent. The *applied* cap
  comes from the per-market gate — `min(k * max(order qty, this market's
  recorded clip), 5)`, which is 1 contract at a 1-contract clip — and is not
  compared against this field at step 1b.

## Revoking

1. **Halt latch** on the watchdog — fastest, no redeploy, executor cannot clear.
2. **Revocation list** — add `"l5-standing-001"` or `"l5-standing-001#4"`.
   (Served from the watchdog volume; lands in phase 2.)
3. **Raise `seq`** with a new mandate, or clear `L5_MANDATE_DOC`.
