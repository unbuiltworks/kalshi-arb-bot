# L5 two-sided live campaign — FINAL APPROVAL DOCUMENT

**NOT ARMED. No live order has been submitted. Arming is impossible until you
sign the mandate — `MANDATE_PUBKEYS` is empty, so no mandate can verify.**

---

## A. D2 watchdog port — DEPLOYED, checks passed

Deployed `782437af` → SUCCESS 22:08:47Z, then `e778e866` (telemetry).
**The watchdog's independent source and deployment configuration are
preserved. It was not repointed.**

### Your four pre-deployment checks

**1. Verified against the watchdog branch's actual code and dependencies.**
Ported minimally and deliberately: taking my branch's watchdog files wholesale
would have brought an emergency-halt-marker subsystem whose module **does not
exist on that branch** — an ImportError at boot, and not what you approved.
Only four files moved:

| file | change |
|---|---|
| `src/l5_mandate_signature.py` | added |
| `src/l5_halt_server.py` | serve the two documents under an exact-key allowlist |
| `scripts/l5_halt_watchdog.py` | publish both on every sweep |
| `deploy/l5_halt_watchdog/Dockerfile` | COPY the one new module |

`cryptography>=42` was already installed in that image and the import is lazy
inside `_verify_ed25519`.

**2. Tests run on that branch, not just mine:** entrypoint imports; **13**
publishing tests; **435 existing halt/watchdog tests pass unchanged**; a new
transitive image-closure test over 11 modules, **proven to have teeth** —
deleting the new COPY line fails it.

**3. No halt or reset behaviour weakened.** The `HALT_KEY` path is otherwise
byte-identical, `do_POST`/`do_PUT`/`do_DELETE` remain undefined, and the
allowlist is strictly narrower than before (it also closes a traversal shape,
since `FileStore._path` maps a key straight onto a filesystem path). **A
redeploy clears no halt:** the last reset event is `REFUSED —
NO_LATCH_PRESENT`, "a spent reset id refusing here is the expected steady
state". Variable *values* are redacted to me, so I confirmed this from the
watchdog's own log rather than by reading `L5_HALT_RESET_AT_BOOT`.

**4. No change to live executor authorization or trading limits.** The port
touches only the watchdog. `MANDATE_PUBKEYS` ships empty, so the executor's
signature gate stays inert and every existing authorization path is unchanged.

### Boot confirmation, 22:08:45Z

```
mandate_documents_served : ["l5/halt.json", "mandate_floor.json",
                            "mandate_revocations.json"]
mandate_doc_max_age_s    : 900.0
mandate_trusted_keys     : 0
cancel_enabled/order_source/poll_s : unchanged (true / CANCEL_ONLY / 20.0)
```

---

## B. Endpoint verification

The documents are on **private networking** and the watchdog has **no public
domain** — correctly, since one would expose the halt latch, and I did not
create one. So I cannot reach them from here, and the executor is the only
client that can.

`L5_MANDATE_STORE_PROBE` now runs at executor boot and fetches both documents
through the **same GET-only clients the arming gate uses**, so a success is
the gate's own answer: the documents exist, parse, carry a usable `asof_utc`
and sit inside the 900 s bound. It authorizes nothing and writes nothing,
pinned by walking its code object.

### VERIFIED, 2026-10-24T22:17:45Z

```
L5_MANDATE_STORE_PROBE
  configured  : true
  host        : l5-halt-watchdog.railway.internal:8080
  floor       : {ok: true, value: 0}
  revocations : {ok: true, value: []}
  verdict     : BOTH_DOCUMENTS_FRESH
```

Both endpoints are **reachable, well-formed and inside the 900 s staleness
bound**, read by the real consumer over the real private network using the
arming gate's own clients.

- `floor: 0` for an unseen key is a **positive statement** by the watchdog
  that it has seen no sequence for it — not a default.
- `revocations: []` is a **positive statement** that nothing is revoked,
  published with a fresh stamp — deliberately not the same as the document
  being absent, which refuses.
- `trusted_key_ids: null` — the gate is still correctly **inert**.

Freshness is maintained by the publish sitting inside the sweep loop at a
20 s cadence against the 900 s bound, so a watchdog that stops refreshing
**stops authorizing**.

---

## C. The mandate

| parameter | value |
|---|---|
| **Max aggregate exposure, incl. outstanding orders** | **$5.00** — every gate prices a resting order as filling in full |
| **Max simultaneous orders** | **2**, only ever the two legs of one pair, one market, one round |
| **Per-order quantity** | **2 contracts** — venue minimum is 0.01, but a maker print below ~1.6 earns $0.00 credit |
| **Submission budget, OPEN** | **16** ✓ |
| **Rounds per window** | **2** (`L5_TS_ROUND_ALLOCATIONS=2`) |
| **Worst-case deficit bound** | **$2.00** ✓ (new) |
| **Residual cap** | **3 contracts, PRESERVED** ✓ — the dollar bound is additive |
| **Daily realized loss halt** | $5.00, on the UTC-day figure |
| **Cumulative account-wide** | $20.00 — $11.6213 used, **$8.3787 remaining** |
| **Campaign worst-case budget** | $5.00 |
| **Duration** | ≤ 8 windows and ≤ 4 hours, whichever first |
| Series | `cpc-btc-updown-15m-` only |
| Execution | **maker-only, post-only both legs**; no action name expresses crossing |
| Pair cost cap | ≤ $0.96 per pair |
| Concurrent markets | 1 |

### Why 16 is exactly right, not a sacrifice

At q=2 and the 0.96 cap a pair commits **$1.92**, and cash is released only on
settlement — so the $5 envelope allows **2 pairs per window**, and 2 × 8
windows = **16 OPEN**. **The submission budget and the cash gate bind at the
same place.** 32 would have been headroom the $5 envelope cannot use. I have
lowered the runtime from 32 to 16 (a tightening) and set the $2.00 bound.

### B7 — accepted, with the dollar protection added

The 2026-10-23 campaign settled **+$0.1336**; resting both legs would have
given **−$1.6981** (−$0.5335 forfeited complement improvement, −$1.2982
dear-side stranding) — a **−$1.8317 swing**. You have accepted that risk.

The protection, now configured: the residual cap is denominated in
**contracts** and is therefore price-blind — 3 contracts admits $0.36 at own
price 0.12 and **$2.49** at 0.83. `max_worst_case_deficit_usd = $2.00` makes
it price-aware:

| | worst case | verdict |
|---|---|---|
| 2 contracts @ 0.80 | $1.64 | allowed — the proposed clip |
| **3 contracts @ 0.80** | **$2.45** | **refused — binds before the contract cap** |
| 3 contracts @ 0.12 | $0.36 | allowed |

**Tested as you asked:** simultaneous fills (a filled pair is not refused —
the bound must not block the completion that removes the exposure); partial
fills (a smaller confirmed book); pending cancellations (charged in full, so
two dear clips breach where one does not); worst-case aggregate obligations
(outstanding orders are an adversary, not an assumption); an outstanding order
of unknown side fails closed; and **a configured but un-evaluable bound
refuses** (`WORST_CASE_DEFICIT_NO_INVENTORY`). 43 tests in that file, 7 new.

### Trading behaviour (unchanged L5 engine)

Rest both sides when justified · work multiple price levels · respond
continuously to fills and movement · **EXTEND and REDUCE both available** ·
replenish automatically · continue across windows without restart · **do not
force pairs, fills or rebalancing when economics are unfavourable** (the pair
cap and the deficit bound each refuse by name; the price floor will too
once set — see the correction below).

### Automatic halts

Watchdog HALT latch (executor cannot clear) · daily loss ≤ −$5 · campaign
budget exhausted · account-wide −$20 · residual ≥ 3 contracts · **worst-case
deficit > $2.00** · authorization expiry · window budget spent · per-window
timing (entry 600 s, complete 720 s, cancel margin 300 s, TTL 120 s, runway
300 s) · unresolved accounting · no auto-relaunch after a completed or crashed
session.

### Reconciliation

Venue-authoritative on every uncertainty and periodically; exact-order
cancellation by venue id; an order absent from the venue's open orders is
terminal but **not released until an authoritative `/v1/order` read books its
final `cumQuantity`**. Capital is released on **settlement evidence alone**,
never on a `matched` flag — **unsettled inventory is never freed capital**.

### What will be measured

Real submitted orders · maker fills and partial fills · completed pairs ·
pair acquisition cost · residual inventory · **credited** rebates (observed
`commissionNotionalCollected`, never advertised) · settlements · **confirmed
realized net P&L**. Nothing simulated or unsettled will be called profit.

### Configuration still to be set, ONLY on your signature

| change | from | to |
|---|---|---|
| `L5_TS_TWO_ORDER_PAIR` | off | `1` |
| `L5_TS_ROUND_ALLOCATIONS` | 1 | `2` |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | 3 | `2` |
| `L5_TS_MIN_FIRST_LEG_PRICE` | **0** (not 0.20) | `0.20` |
| `L5_LIVE_AUTH_NOT_AFTER` | expired | your window |
| campaign approval document | stale | fresh |

Already set (tightenings only): `L5_LIVE_AUTH_MAX_OPEN=16` — **verified
in-container, `effective: 16, ok: true`**, where the same row read
`32 / FAIL` an hour earlier — plus `L5_MAX_WORST_CASE_DEFICIT_USD=2.00` and
`L5_MANDATE_STORE_URL`.

### Two things the in-container config corrected

I had both from module defaults rather than from the running process.

1. **The first-leg price floor is `0` in production, not `0.20`.**
   `L5_TS_MIN_FIRST_LEG_PRICE` overrides the module default, so the $0.20
   floor is **not in force** and is listed above as a change to make. I have
   not set it: it changes what the engine may open, so it belongs to your
   signature rather than to a tightening I apply unasked.
2. **The $2.00 deficit bound was not observable in-container.** The verifier
   graded 23 limits and the one *new* risk control of this campaign was not
   among them — so I could assert its value but not read it, which is the
   exact failure mode that module exists to prevent. It is now an observed
   row read through the money path's own parser, so a malformed value appears
   as the refusal it would be at boot rather than as a number.

---

## D. Owner signing instructions

**I never create, store, request or see your private key.** It is generated by
you, on your machine, and must never enter this repository, Railway, a log, a
chat or any assistant.

### Step 1 — generate the keypair (once, offline)

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
print("PUBLIC KEY (send me this):",
      p.public_key().public_bytes(encoding=S.Encoding.Raw,
                                  format=S.PublicFormat.Raw).hex())
EOF
```

Back the key file up offline. **If it ever reaches Railway, GitHub, a log, a
chat or an assistant, treat it as burned:** delete its `MANDATE_PUBKEYS`
entry, generate a new pair, and bump `seq` past the highest the old key signed.

### Step 2 — send me the 64-hex PUBLIC key only

I commit it as `key_id` `owner-2026-10` in a reviewed change. Until an entry
exists every mandate refuses `MANDATE_NO_TRUSTED_KEY` — today's state.

### Step 3 — the payload

`MANDATE_PAYLOAD_5USD.json` is exact. **Fill in `not_before` and `not_after`
(≤ 4 h apart) and change nothing else.** Enforced, each a refusal: every field
required and **no extra field**; **money as strings** (a JSON float is
`MANDATE_MONEY_INVALID`); timestamps with an **explicit offset**; duration
≤ 30 days; `execution_mode` must be `MAKER_ONLY`;
`requires_bounded_live_validation` must be `true`; `seq` strictly increases.

### Step 4 — sign

```bash
python3 tools/owner_sign_mandate.py \
  --key ~/.l5/mandate_ed25519.key \
  --payload MANDATE_PAYLOAD_5USD.json \
  --key-id owner-2026-10 \
  --out signed_mandate.json
```

It refuses a key inside the repo tree or a group/world-readable key file, and
dry-runs the real verifier so a payload the executor would reject is caught on
your machine.

### Step 5 — install

Paste `signed_mandate.json` into **`L5_MANDATE_DOC`** on `l5-pmus-live`.

### To revoke

1. **Halt latch** on the watchdog — fastest, no redeploy, executor cannot clear.
2. `L5_MANDATE_REVOKED` on the **watchdog** (the executor cannot write it).
3. Raise `seq` with a new mandate, or clear `L5_MANDATE_DOC`.

---

### In-container configuration, 22:17:45Z

| row | effective | verdict |
|---|---|---|
| `submission_budget_open` | **16** | ok ✓ |
| `daily_realized_loss_halt_usd` | 5 | ok |
| `maker_pair_ceiling` | 0.96 | ok |
| `max_live_orders` | 1 | ok |
| `cancel_margin_s` | 300.0 | ok |
| `maker_only_forbids_defensive_crossing` | true | ok |
| `mandate_signing_key_absent_from_runtime` | **true** | ok |
| `aggregate_exposure_usd` | **5** vs expected 20 | FAIL — **runtime is tighter**; the expectation is stale against your $5 restoration |
| `mandate_trusted_key_ids` | **(none)** | gate inert |
| `mandate_enforcing` | **false** | gate inert |
| `ts_two_order_pair_enabled` | **false** | not armed |
| `ts_round_allocations` | 1 | → 2 on signature |
| `ts_opening_leg_max_contracts` | 3 | → 2 on signature |
| `ts_min_first_leg_own_price` | **0** | → 0.20 on signature |
| `ts_active_any_market` / `ts_named_market` | true / `cpc-btc-updown-15m-*` | scoped |

The single graded failure is the exposure row, where the runtime is **tighter**
than the figure the verifier carries. No dollar bound is breached.

---

## E. The remaining blocker

**One: your public key.** Everything else is deployed, tested or is a term of
this document. On receiving the key I commit it, you sign, and the campaign
runs within these limits and reports actual filled, settled, realized results.

## Accounting labels

Venue-confirmed realized results to date: **+$0.1336** (2026-10-23, 8 windows,
sequential two-sided) and **−$3.7194** (Stage 1, 8 windows). Maker credits are
**OBSERVED** venue credits — $0.42 over 25 prints. Every figure in §C is a
**limit**, not a forecast. At $5 the campaign cannot measure the edge (signal
÷ residual noise ≈ 0.087); its honest purpose is **mechanical validation** of
continuous L5-style execution.
