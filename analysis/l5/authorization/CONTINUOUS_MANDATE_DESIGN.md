# Eliminating the per-campaign manual install

Design only. Nothing here is deployed, no key exists, no limit changes.

## 1. The secrecy model we are paying for does not deliver its property

`src/l5_mandate_signature.py` already records this, and it is the finding
that decides the design:

> `l5_campaign_approval` reads the approval from a Railway variable and its
> docstring claims "Claude cannot write it, so Claude cannot approve a
> campaign". **That is false in practice**: anyone holding a Railway token
> can set the variable, and in session 012Mo8AP three were set that way.
> Moving the document to another store only moves the same problem, because
> the executor's operator can reach every store the executor can.

This session holds a Railway token through the connector. The only thing
preventing me from approving a campaign today is that **I decline to read
the file** — a policy restraint, not a technical control. We pay for that
notional control with a manual paste every four hours.

The nonce is a **bearer secret**: authority comes from possessing the bytes.
That is why it cannot pass through chat, and why installation cannot be
delegated. A **signature** moves authority from possession to verification:
the bytes stop being secret, so anyone may carry or install them, while only
the private-key holder can author them.

That single change removes the bottleneck without weakening anything —
it replaces a control that does not bind the real threat with one that does.

## 2. What already exists (more than the docs claim)

| piece | state |
|---|---|
| `src/l5_mandate_signature.py` | complete Ed25519 verifier, fail-closed on 11 named conditions |
| `tools/owner_sign_mandate.py` | exists; refuses an in-repo or group-readable key, dry-runs the real verifier |
| `docs/OWNER_MANDATE_SIGNING.md` | complete offline owner procedure |
| arming gate | **already live code** at `l5_live_authorization.py:2036`, inert only because `MANDATE_PUBKEYS` is empty |
| replay floor + revocation client | `FLOOR_KEY`, `REVOCATION_KEY`, GET-only from the watchdog |
| **watchdog server side** | **exists** — `src/l5_halt_server.py:40` `SERVED_KEYS = (HALT_KEY, FLOOR_KEY, REVOCATION_KEY)` |
| watchdog service | `deploy/l5_halt_watchdog` |
| `L5_MANDATE_STORE_URL` | already a configured variable on `l5-pmus-live` |
| tests | **321 pass** across 8 mandate suites |

The signing doc says the revocation list "lands in phase 2". That note is
**stale** — the server side is implemented and tested
(`tests/test_l5_mandate_watchdog_publish.py`). Confirm the deployed watchdog
actually serves it before relying on it, via the existing
`L5_MANDATE_STORE_PROBE` the runner already emits
(`l5_pmus_live_runner.py:7047`).

## 3. Why turning it on today would make the bottleneck WORSE

The mandate is **additive, not a substitute**. Arming
(`l5_live_authorization.py:2160–2195`) requires **one of**:

- a valid campaign document (`L5_CAMPAIGN_APPROVAL`), which "authorizes
  campaign windows on its own"; or
- the legacy Q1 one-window handshake

and **additionally**, once `MANDATE_PUBKEYS` is non-empty, a valid signed
mandate. `tests/test_l5_mandate_arming_gate.py:270` says it outright: *"the
signature is a NECESSARY ADDITIONAL condition"*.

So registering a public key today would require **both** a nonce document
every four hours **and** a signed mandate. That is the thing to fix, and it
is the whole change.

## 4. The minimal change

**Make a verified mandate a third authorization source in the existing
disjunction**, so a standing mandate authorizes windows on its own against
its per-day rates.

It is small because the payload already carries every field the campaign
scope needs:

| mandate field | plays the role of |
|---|---|
| `series`, `execution_mode`, `allowed_actions` | campaign series / MAKER_ONLY / action classes |
| `max_windows_per_day` | `max_windows`, but per UTC day |
| `max_submissions_per_day` | the submission backstop, per UTC day |
| `capital.max_cash_deployed_usd` / `max_total_exposure_usd` / `campaign_budget_usd` | the same three ceilings |
| `loss_limits.daily_realized_loss_halt_usd` / `max_cumulative_loss_usd` | unchanged halts |
| `residual_cap_contracts` | residual cap |
| `not_before` / `not_after` (≤30 days) | validity window |

The only thing it lacks is a **claim identity**, and it already has the right
one: `mandate_id` + `seq`. The revocation list already formats exactly that
as `l5-standing-001#4`.

So:

1. **`mandate_claim_sid()`** → `mand1.<mandate_id>#<seq>.<utc_date>`.
   The UTC-date component is what makes `max_windows_per_day` and
   `max_submissions_per_day` bind **per day** using the *existing* lock-file
   counters `windows_spent()` and `submissions_spent()` — no new storage, no
   new counter, no new reconciliation surface.
2. **Extend the disjunction** at `2160–2195` to accept a verified mandate,
   keeping the existing "present and not ok refuses by its own name" order so
   an expired or revoked mandate never reports as "no authorization".
3. **Map mandate fields onto the same scope dict** the campaign path builds,
   so `mandate_scope()`'s digest, `MANDATE_SCOPE_CHANGED`, the durable window
   claim, and the campaign-budget `open_or_resume` / `release_stale`
   lifecycle all work **unchanged**.

Estimated size: ~60–120 lines across `l5_live_authorization.py` and
`l5_mandate_signature.py`, plus tests. No new service, no new storage, no new
approval layer, no dollar limit touched.

## 5. One-time owner setup that is genuinely unavoidable

Two things cannot be delegated, because they are the entire security
property:

1. **Generate the keypair offline.** One documented command
   (`docs/OWNER_MANDATE_SIGNING.md` step 1). The private half must never
   enter this repo, Railway, a log or a chat. The runtime already verifies
   this: `mandate_signing_key_absent_from_runtime: true`.
2. **Hand over the 64-hex PUBLIC key.** I commit it to `MANDATE_PUBKEYS` in
   a reviewed change. A repo commit is deliberately the only way to arm the
   gate — *"no variable edit can satisfy it"*.

Then, per mandate (about monthly):

3. Sign one payload with the existing tool. The tool dry-runs the real
   verifier, so a payload the executor would reject is caught locally.
4. **Install it.** This is *not* a credential transfer — the document's
   authority is its signature, so it can be pasted anywhere and **I can
   install it myself** via the Railway connector. Writing it is insufficient
   without the key.

Net effect: **one owner action roughly monthly instead of one per four
hours**, and zero file transfers through chat.

## 6. Safety: what is preserved, and what genuinely increases

Preserved, all unchanged:

- every dollar limit, independently enforced by `ValidationGates` and the
  campaign budget ledger, which the mandate can only tighten
- the halt latch as the immediate withdrawal path — the mandate gate
  **requires** `L5_HALT_URL` and refuses `MANDATE_REVOCATION_UNAVAILABLE`
  without it. This process cannot clear the latch
- durable reconciliation and the settled-cash accounting
- `seq` strict monotonicity, so a rollback to a looser document refuses as
  `MANDATE_REPLAYED_OR_ROLLED_BACK`
- replay floor and revocation list on the **watchdog's** volume, where the
  executor's only verb is GET: *"a floor this process can write is a floor
  this process can roll back"*
- unknown field is a refusal, money must be a string, timestamps need an
  explicit offset, `MAKER_ONLY` enforced, `requires_bounded_live_validation`
  must be true
- `l5_campaign_approval` untouched, remaining the short-bounded path

Genuinely increased, and it should be stated plainly: **standing authority
goes from 4 hours to up to 30 days.** The mitigations are the per-day rate
limits, the unchanged dollar ceilings, the halt latch, the revocation list,
and `seq`. A shorter `not_after` (e.g. 7 days) is available at the owner's
discretion and costs only more frequent signing.

## 7. Risks I would not paper over

- **Ordering is load-bearing.** Registering a public key makes the mandate
  gate REQUIRED immediately. If the authorization-source change is not
  already deployed, live arming stops until a signed mandate exists. Correct
  order: ship the code change first (gate still inert, no keys) → owner
  generates key → commit key and install mandate together → verify.
- **The watchdog path is on the critical path.** The gate is fail-closed on
  an unreadable floor, which is right, but it means a watchdog that is not
  actually serving `mandate_floor.json` stops all live arming. Verify with
  `L5_MANDATE_STORE_PROBE` before registering a key, not after.
- **A stale served document refuses.** By design *"a document nobody
  refreshes goes STALE and refuses, so a dead watchdog cannot keep
  authorizing"*. That is correct and it means watchdog liveness becomes a
  trading dependency.
- **I have not tested the proposed change**, because it does not exist yet.
  The 321 passing tests cover the verifier and the additive gate, not
  mandate-as-authorization-source.

## 8. This does not help Campaign 9 today

Campaign 9 still needs the one manual paste of
`campaign9_approval.json`, or it lapses at **15:04:42Z** and I mint a fresh
document on the same approved scope. The two are independent: the design
above is the permanent fix and needs a keypair that does not exist yet.

## 9. What I need from the owner to proceed

Nothing is deployed and no key exists. To move:

- **approve the change in principle**, and I ship step 4 (code only, gate
  still inert, nothing can arm differently until a key is registered); or
- **say no to 30-day standing authority**, and the bottleneck stays one paste
  per campaign — which is a legitimate choice, just an expensive one
