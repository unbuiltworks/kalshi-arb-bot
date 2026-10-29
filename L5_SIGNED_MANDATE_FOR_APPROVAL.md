# $5 two-sided live campaign — mandate for owner signature

**Status: NOT ARMED, nothing submitted, no variable written.** Signature
verification is implemented and tested. Two deployment prerequisites remain
(§5) and I have not deployed them.

---

## 1. Live two-sided capability — verified, not assumed

You asked me not to confuse the ladder's simulated quotes with live order
capability. They are different code paths and I checked the live one.

`second_leg_slot_refusal` grants a **second** maker slot exactly when: the
flag is on, **exactly one order is live**, it is in **this** market, on the
**opposite** side, **claimed in this round**, and this side is not yet
claimed. So leg 2 is submitted **while leg 1 is still resting** — genuine
simultaneous two-sided live quoting, not "place the complement after the
first fills".

It is explicitly **not** `max_live_orders = 2`, which would allow two
independent first legs sharing one cash envelope — the 1215z failure mode.

**73 existing tests already cover the live mechanics**, including:

| requirement | test |
|---|---|
| two legs outstanding | `test_with_the_evidence_a_second_order_passes` |
| no third order | `test_a_third_order_is_refused_even_with_the_evidence` |
| **partial fills** | `test_a_partial_fill_reduces_only_its_own_row` |
| **exact-order cancellation** | `test_a_cancel_names_one_order_and_proves_it_terminal` |
| either fill order | `test_either_fill_order_is_handled_by_reading_held_quantity` |
| independent order identity | `test_each_order_is_keyed_by_its_own_client_and_venue_id` |
| maker-only | `test_maker_only_and_no_crossing_are_unchanged` |
| no gate bypass | `test_the_slot_does_not_bypass_any_other_gate` |

All pass. The capability is real.

---

## 2. ⚠ Evidence that argues against enabling it

I found this while verifying capability and you should see it before signing.

**There was a real 8-window live two-sided campaign on 2026-10-23, and it
settled POSITIVE: +$0.1336.** 5 of 8 windows completed (+$0.8020); the 3
unmatched cost −$0.6684. Those are arithmetic restatements of the ledger, in
`tests/test_l5_two_order_pair.py`.

**It earned that by pricing the complement against the current book from the
realised first-leg cost.** Four of the five completions beat the planned 0.95
— at 0.91, 0.76, 0.74, 0.89:

| | net |
|---|---|
| actual completions | **+$0.8020** |
| the same pairs locked at plan prices | +$0.2685 |
| **forfeited by locking both legs up front** | **−$0.5335** |

That forfeit is **four times the entire campaign's net.** Resting both legs
simultaneously locks the pair at the planned price and gives up the
complement improvement that produced the profit.

**And it adds a new failure mode.** Today the *cheaper* leg is placed first,
so an unmatched window can only strand the cheap side. With both legs
resting, the **dear** side can fill alone, and the residual loss is quantity
× its own price. Across the planned pairs the dear/cheap ratio runs **1.1× to
6.9×** (2230z: 0.83 against 0.12).

A test currently asserts `two_order_pair_enabled() is False`, named
`test_so_the_mechanism_is_not_obviously_profitable`, with the comment: *"A
measured cost of $0.5335 in forfeited complement improvement, plus a new
dear-side stranding risk, against an UNMEASURED gain in completion rate. This
is why activation is withheld."*

**I have not enabled the flag or deleted that test.** Enabling it means
knowingly trading a measured +$0.1336 mechanism for an unmeasured one. Your
call — but you should make it knowing the sequential mechanism is the only
live configuration on record that made money.

Options:
- **B1** — proceed as instructed: enable simultaneous quoting, accept the
  forfeit, treat the campaign as mechanical validation (per the capital
  analysis, economics are unmeasurable at $5 either way).
- **B2** — run the $5 campaign on the **sequential** mechanism that has the
  +$0.1336 result, and keep simultaneous quoting in shadow.

I recommend **B1 only if the goal is mechanical validation**, which is what
$5 can actually deliver. If the goal is positive net economics, B2 is the
configuration with evidence behind it.

---

## 3. Signature verification — implemented

New **step 1b** in `authorize_arming`, before the owner-authorization step so
an unsigned mandate names itself rather than surfacing as an inner failure.

- **Self-activating.** Binds exactly when `MANDATE_PUBKEYS` is non-empty.
  Empty today, so every existing path is unchanged — the moment your public
  half is committed, a signature becomes **required**, and turning it off
  takes a repo commit and a deploy, not an environment edit.
- **A necessary additional condition, not a replacement.** The campaign
  document still bounds windows and reserves one durably; writing that
  variable alone no longer authorizes anything. **That is the fix.**
- **Scope cannot be widened.** Running exposure or daily loss above the
  signed figures refuses `MANDATE_LIMITS_WIDER_THAN_SIGNED`.
- **Maker-only is re-asserted** from the signed term.
- **Replay floor and revocation list come from the watchdog**, over the
  GET-only clients that already exist, never from our own disk — a floor this
  process can write is one it can roll back, which would let an older, looser
  signed mandate be replayed. Absence, staleness, malformation and an
  unreachable host all **refuse**.
- **The halt latch is still required** as the immediate withdrawal path.

**18 new tests**, including that writing the variable without the key cannot
satisfy it (invalid signature, no signature, the campaign-document shape,
`true`, `{}`, non-JSON — all refuse), that tampering with capital after
signing breaks the signature, that an absent revocation list is **not**
"nothing revoked", and that the floor is read over HTTP and not from disk.
998 pass across the authorization/campaign/two-order sweep.

---

## 4. The mandate

| parameter | value |
|---|---|
| **Max aggregate exposure (incl. outstanding orders)** | **$5.00** — every gate prices a resting order as filling in full |
| **Max simultaneous orders** | **2**, only ever the two legs of one pair, one market, one round |
| **Per-order quantity** | **2 contracts** (smallest clip with positive worst case; venue minimum is 0.01) |
| **Residual inventory limit** | **3 contracts absolute**; clip 2 so one side filling cannot reach it |
| **Daily realized loss halt** | **$5.00**, on the UTC-day figure |
| **Cumulative account-wide** | **$20.00** — $11.6213 used, **$8.3787 remaining** |
| **Campaign worst-case budget** | **$5.00** |
| **Duration** | **≤ 8 windows and ≤ 4 hours**, whichever first |
| Series | `cpc-btc-updown-15m-` only |
| Execution | **maker-only, post-only both legs**; no action name expresses crossing |
| Pair cost cap | ≤ $0.96 per pair |
| Concurrent markets | 1 |

**Halt conditions:** watchdog HALT latch (executor cannot clear) · daily loss
≤ −$5 · campaign budget exhausted · account-wide −$20 · residual ≥ 3 ·
authorization expiry · window budget spent · per-window timing (entry 600 s,
complete 720 s, cancel margin 300 s, TTL 120 s, runway 300 s) · unresolved
accounting · no auto-relaunch after a completed or crashed session.

**Reconciliation:** venue-authoritative on every uncertainty and periodically;
exact-order cancellation by venue id; an order absent from the venue's open
orders is terminal but **not released until an authoritative `/v1/order` read
books its final `cumQuantity`**.

---

## 5. Prerequisites I have NOT done

1. **The watchdog must publish two documents** — `mandate_floor.json` and
   `mandate_revocations.json` — and `L5_MANDATE_STORE_URL` must point at it.
   Nothing serves them today: `WatchdogSeqWriter` and the HTTP clients exist
   only in the module. Until then, arming with a registered key refuses
   `MANDATE_STORE_UNCONFIGURED`. This is a bounded change to the **existing**
   `l5_halt_watchdog` service (add `l5_mandate_signature.py` to its image and
   two GET routes to `l5_halt_server.py`). Say the word and I'll do it.
2. **Your public key must be committed** to `MANDATE_PUBKEYS`.

I have not deployed either, per *"do not deploy or arm until signature
verification and reconciliation pass."*

---

## 6. Your offline steps

**I never generate, see, hold or transmit your private key.** Full procedure
is in `docs/OWNER_MANDATE_SIGNING.md`; the essentials:

### Step 1 — generate the keypair (once, on your machine)

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

Back it up offline. **If the private key ever reaches Railway, GitHub, a log,
a chat or any assistant, treat it as burned**: delete its `MANDATE_PUBKEYS`
entry, generate a new pair, and bump `seq` past the highest the old key signed.

### Step 2 — send me the 64-hex **public** key only

I commit it as `key_id` `owner-2026-10` in a reviewed change. Until an entry
exists, every mandate refuses `MANDATE_NO_TRUSTED_KEY`.

### Step 3 — the payload

`MANDATE_PAYLOAD_5USD.json` in the repo is the exact payload for this
mandate. **Fill in `not_before` / `not_after`** (≤ 4 h apart) and nothing
else. Rules the verifier enforces, each a refusal: every field required and
**no extra field**; **money as strings**; timestamps with an **explicit
offset**; duration ≤ 30 days; `execution_mode` must be `MAKER_ONLY`;
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
2. Add `l5-twosided-5usd-001` to the revocation list.
3. Raise `seq` with a new mandate, or clear `L5_MANDATE_DOC`.

---

## 7. What I need from you

1. **B1 or B2** — simultaneous quoting (as instructed, accepting the $0.5335
   forfeit and dear-side stranding), or the sequential mechanism with the
   +$0.1336 result.
2. **Go-ahead on the watchdog change** (§5.1), without which the signature
   gate refuses.
3. **Your public key** (§6.2).

## Accounting labels

Every figure in §4 is a **limit**. Venue-confirmed realized results to date:
**+$0.1336** (2026-10-23, 8 windows, sequential two-sided) and **−$3.7194**
(Stage 1, 8 windows). Modelled one-sided edge is **negative** (−$0.0827 per
filled contract, 91.6% adverse over 972 modelled fills). Nothing here
forecasts a profit.
