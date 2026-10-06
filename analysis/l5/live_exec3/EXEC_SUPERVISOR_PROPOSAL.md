# Removing deploy latency from EXEC experiments — PROPOSAL ONLY

Not implemented. Not deployed. No code written. XR2 `0f334019…042f` at SHA
`44cbdc81` is unchanged and Attempt 5 is untouched.

## The problem, stated precisely

XR2's own scheduler works: the pre-open wait held for 2450 s / 490 polls and
entered eligibility exactly at window open. What fails is **getting the
process started**. Railway redeploy latency today: ~25 min, ~13 min, ~54 min.
A BTC15m window gives a 13-minute placement period. So the container start,
not the experiment, is the critical path — and Attempt 4 died of it.

The root cause is architectural: **the Railway environment-variable + redeploy
cycle is being used as the runtime scheduler.** Variables only take effect on
a new deployment, so every authorization costs a deployment.

## Option 0 — no code change at all (the baseline to beat)

Keep doing what Attempt 5 does: pick a window ≥90 minutes out, arm, redeploy
immediately, let `_await_window()` absorb the deploy latency.

* Cost: zero new code, zero new risk, zero new fingerprint.
* Limit: ~90+ minutes from authorization to eligibility, and it still fails if
  a deploy ever exceeds the lead time.

Everything below should be judged against this. Option 0 already satisfies
"seconds from boot to waiting"; what it does not give is **seconds from
authorization to eligibility**.

## Option B — RECOMMENDED: long-lived DISARMED supervisor + signed
## authorization document polled over HTTPS

### Shape

```
container boots once and stays up
 └─ AuthBook connected continuously (already self-recovering)
 └─ DISARMED IDLE loop, every 5 s:
      fetch the authorization document (outbound HTTPS GET)
      verify it (below)
      if no valid, unused document -> stay idle, log a heartbeat
      if valid -> run EXACTLY ONE experiment for that document:
           _await_window(its slug) -> gates -> preview -> TTE recheck
           -> latch consume -> TTE recheck -> ONE POST -> lifecycle
           -> markouts -> withdrawal -> deadman in finally
      mark the document's nonce CONSUMED on the volume, return to IDLE
```

Authorization-to-eligibility becomes **one poll interval (≤5 s)** plus the
owner's commit/publish time. No redeploy.

### The authorization document

Canonical JSON, with a detached Ed25519 signature over its canonical bytes:

```json
{
  "probe": "LIVE-L5-EXEC-1-XR3",
  "spec_fingerprint": "<must equal the running build's own fingerprint>",
  "authorization_id": "<nonce, single use>",
  "session_id": "<new, never used>",
  "market_slug": "cpc-btc-updown-15m-YYYY-MM-DD-HHMMz",
  "quantity_shares": "0.01",
  "max_possible_loss_usd": "0.0099",
  "not_before_unix": 0,
  "not_after_unix": 0,
  "max_orders": 1
}
```

### Verification chain — every one of these must pass, or the document is
### ignored and the service stays DISARMED

1. **Signature** verifies against the owner's Ed25519 **public** key, which is
   compiled into the frozen spec and therefore inside the fingerprint. The
   private key never touches the service, the repo, or this session. Claude
   cannot mint an authorization; that is the whole point.
2. **`spec_fingerprint` equals the running build's own fingerprint** — the
   same bind as today's `PMUS_EXEC1_AUTHORIZED_FINGERPRINT`, now signed.
3. **`authorization_id` is unused**, checked against a durable nonce ledger on
   `/state/pmus-ws` with the same `O_CREAT|O_EXCL` discipline as the latch.
   A replayed document is refused.
4. **`not_before` / `not_after`** bracket now. An authorization expires on its
   own; a forgotten one cannot fire days later.
5. **Economics match the frozen spec exactly**: quantity `0.01`,
   max loss `0.0099`, `max_orders` 1. A document that asks for anything else
   is refused rather than obeyed — the signature authorizes *this* experiment,
   it does not get to redefine the envelope.
6. **Side is still derived**, never carried: `sha256(slug | session_id)`. The
   document cannot name a side, so a signed document cannot smuggle a
   directional view in.
7. Then **every existing gate, unchanged**: durable session latch unspent,
   fresh two-sided AuthBook ≤2.0 s at the decision point, venue-derived
   tick/grid, `minimumTradeQty`, price ≤0.99 on the grid, local would-cross
   refusal, post-only, preview echo, **TTE >120 s at run start, before the
   latch, and before the POST**, one order, no retry, no replacement, no
   complement, no next-market selection, deadman in `finally`.

### Fail-closed properties preserved

| requirement | how |
|---|---|
| no authorization = cannot submit | the order transport is constructed only inside an authorized experiment; idle loop has no order route |
| fingerprint must match | signed field, compared to the build's own |
| session + slug frozen before eligibility | both are inside the signed bytes; changing either invalidates the signature |
| deterministic side frozen from those inputs | derived, not transmitted |
| durable one-submission latch | unchanged, session-scoped, on the volume |
| fresh AuthBook ≤2 s | unchanged |
| TTE >120 at actual POST | unchanged, all three checks |
| one order only | `max_orders` 1 + the in-process latch + the durable latch |
| no automatic next-market | the slug comes only from the signed document; no discovery code exists |
| no retry/replacement | unchanged; ambiguous POST still spends the latch and stops |
| deadman/cancellation | unchanged, and still needs no arming |

### Delivery channel for the document

Smallest use of existing infrastructure: a path in **this repository**, fetched
over HTTPS (`raw.githubusercontent.com/.../exec_authorization.json`). The
owner publishes the signed blob; Claude may carry the bytes but cannot author
them. Outbound-only — **no inbound port is opened on a credentialed,
order-capable service**, which is why this is preferred over a webhook
endpoint.

**One thing to verify before committing to this**: that the container's egress
policy permits `raw.githubusercontent.com`. If it does not, the same document
can be served from any already-permitted host, or placed on the mounted volume
by a tiny separate one-shot writer service — but the volume route reintroduces
a deploy, so it is a fallback, not the design.

## What this costs

* **A new fingerprint and a new owner authorization.** The arming mechanism
  and the supervisor loop are in the spec, so the hash changes. Trading
  behaviour, risk limits and execution rules do not.
* **A long-lived order-capable process.** Today the order path exists for ~70
  seconds per attempt; under this design it exists continuously. That is the
  real trade and should be weighed deliberately. Mitigations: idle loop holds
  no order transport; every submission needs a fresh signed, unexpired,
  unused document; the nonce ledger and session latches are durable; and a
  per-process and per-day experiment cap is cheap to add.
* **One owner setup step**: generate an Ed25519 keypair, keep the private key
  off every machine in this system, and hand over the public key for the spec.

## Rejected alternatives, and why

* **Inbound HTTP endpoint on the service.** Fewest moving parts, but it opens
  a listening socket on the one service holding PMUS credentials and an order
  path. Signature verification would carry all the safety; a bug in the
  listener is reachable from the internet. Not worth it when polling works.
* **Railway variables without redeploy.** Not available — variables apply at
  deployment. This is the thing being eliminated, not a tool to use.
* **`railway run` / exec into the live container.** Depends on owner-side CLI
  access and does not give a durable, auditable authorization record.
* **Cron-style fixed schedule.** Removes owner control over *which* window and
  reintroduces automatic market selection, which is explicitly forbidden.

## Recommendation

If the goal is seconds from authorization to eligibility, Option B is the
smallest mechanism that gets there without weakening any gate. If the ~90
minute lead of Option 0 is tolerable, Option 0 remains strictly safer and
needs no new fingerprint. My suggestion: run Attempt 5 to a terminal state on
Option 0 first, so the one-order lifecycle is observed end to end on the
artifact already authorized, and only then decide whether the standing
supervisor is worth a new fingerprint.

---

# BUILT AND TESTED — XR3. Not deployed, not armed, not authorized.

**Candidate fingerprint (placeholder owner key)**
`6e7f5aedc5125e3bf4461f61267b16fb81e98397c2d5bb56dc7cde00118199a4`

Derived from XR2 `0f334019…042f` → XR `f3e54d82…c5e9` → XR-X `d2f32276…6c79`
→ EXEC-1 `b1dbe7f1…2e06`. All four re-verified at boot. `correction_count`
stays **1** — XR2's TTE recheck is still the only behavioural correction in
this lineage; XR3 adds none. Quantity 0.01, max loss $0.0099 unchanged, and
a test asserts 15 whole subtrees (`size`, `orders`, `book`, `evidence`,
`deadman`, `withdrawal`, `accounting`, `behaviour`, `side_rule`,
`placement_rule`, `timing_explicit`, `tte_invariant`, `markouts`,
`one_submission`, `cancellation`) are byte-identical to XR2.

**This fingerprint is provisional.** `OWNER_PUBLIC_KEY_B64` is the
placeholder, and the key is inside the hash — a test proves changing it
changes the fingerprint. So supplying your real key produces the fingerprint
you actually authorize. Until then the supervisor refuses to arm at all and
says so at boot.

## Files

| file | role |
|---|---|
| `analysis/l5/live_exec4/spec.py` | XR3 spec, derived from XR2 by literal DIFF |
| `analysis/l5/live_exec4/authz.py` | document schema, Ed25519 verification, nonce ledger |
| `analysis/l5/live_exec4/supervisor.py` | the DISARMED idle loop |
| `scripts/l5_exec1xr3_supervise.py` | entrypoint |
| `tests/test_live_exec1xr3.py` | 112 tests |

**502 tests pass** across seven suites. XR2 and everything below it is
untouched — same files, same fingerprints.

## The separation you asked for, enforced

Two durable files in two directories, and neither fact is inferred from the
other:

* `/state/pmus-ws/exec1xr3_authz/` — **this authorization is spent**
* `/state/pmus-ws/exec1x/` — **this session made its one submission attempt**
  (unchanged `live_exec2.latch`)

The authorization is consumed **before** the experiment, so a document whose
outcome we cannot observe can never be replayed. The submission latch is
consumed **immediately before the POST**, unchanged. Three tests pin the
separation: consuming an authorization leaves the latch unspent; spending the
latch does not free the authorization; and "authorized, gates refused, no
order attempted" produces exactly a consumed authorization with an unspent
latch — the correct record, not an ambiguous one.

## Threat model

| threat | control | tested |
|---|---|---|
| Claude mints an authorization | only the owner's **public** key is in the image; the private key never touches the service, repo or any Claude session | wrong-key and unsigned documents refused |
| Claude edits a signed document | signature covers canonical JSON of every field | 5 fields tampered post-signing, all detected |
| document reordered in transit | canonical sorted-key bytes | reordered document still verifies |
| replay of an old authorization | durable single-use nonce, `O_CREAT|O_EXCL` | consume-then-replay refused; survives module reload |
| stale authorization fires later | `not_before`/`not_after` required | not-yet-valid, expired and empty-window refused |
| document widens the envelope | economics compared to the **frozen spec**, mismatch refused not obeyed | qty 1.00, qty 0.02, max-loss 1.00, max_orders 2 all refused **though correctly signed** |
| document smuggles a side or a parameter | 7 forbidden fields; side derived from `sha256(slug\|session)` | each forbidden field refused |
| document names a non-BTC15m market | slug regex | 4 bad slugs refused |
| wrong build consumes a document | `spec_fingerprint` and `probe` must match | both refused |
| unwritable ledger lets a replay through | `AuthzLedgerUnavailable`, fail closed | refused |
| nonce file deleted to re-enable a document | no release/clear/reset/delete function exists in the module | asserted by source scan |
| idle process still holds an order route | order transport constructed only inside `_run_one` | AST scan: no reference to `BoundedOrderTransport`, `submit_one`, `/v1/orders` outside `_run_one`, and no top-level import of the orders module |
| unbounded experiments on a long-lived process | 4 per process, 4 per UTC day | cap refusal tested |
| hostile or oversized document body | https-only, 8 KiB cap, JSON parse failure is a refusal | oversized, non-https, unreachable and garbage all stay DISARMED |
| env projection leaks between experiments | the slug/session are projected onto the frozen runner's env for one call and restored in `finally` | restoration tested |

## Failure modes, and what each one does

| failure | behaviour |
|---|---|
| document URL unset | IDLE_DISARMED forever, logged each tick |
| document unreachable / malformed / unsigned | IDLE_DISARMED, no consumption |
| owner key is the placeholder | refuses to arm and says so at boot |
| two processes race on the same document | `O_CREAT|O_EXCL` — one wins, the loser reports `RACED_ON_CONSUME` |
| experiment raises | authorization already consumed, latch state untouched, terminal, back to idle |
| AuthBook fails to start | supervisor stays DISARMED and each experiment's own ≤2.0 s book gate still applies independently |
| volume unavailable | both ledgers fail closed; nothing is authorized |

## Residual risk I am not hiding

The order path exists continuously instead of ~70 seconds per attempt. The
controls above are structural rather than advisory, but the honest summary is
that XR3 trades a *shorter-lived* capability for a *faster* one. If that
trade is not worth it, Option 0 remains available and needs no new
fingerprint.

## Migration / cutover plan

1. **Attempt 5 reaches a terminal state on XR2.** Nothing below starts before
   that; the live experiment stays singular.
2. **You generate an Ed25519 keypair** and keep the private key off every
   machine in this system. Hand over the base64 public key only.
3. I set `OWNER_PUBLIC_KEY_B64`, recompute, and report the **real** XR3
   fingerprint plus the re-run test suite. No other edit.
4. **You authorize that fingerprint** (or decline).
5. Deploy XR3 to `l5-live-probe-oneshot` with `PMUS_EXEC1_AUTHZ_URL` set and
   **no** `PMUS_EXEC1_*` arming values. One deploy, once. Its boot log must
   show `xr3_frozen: true`, `owner_public_key_supplied: true`, and
   `IDLE_DISARMED` ticks.
6. **Verify egress** to the document host from that boot log. If it is
   blocked, stop and choose a permitted host — do not work around it.
7. **Dry run**: you sign a document with `not_after` already in the past. The
   supervisor must refuse it as `EXPIRED` and consume nothing. That proves the
   live path end to end with zero risk.
8. First real authorization: you sign a document for a chosen window; it goes
   live within one poll interval.
9. **Rollback** at any point is `restartPolicyType NEVER` plus redeploying the
   XR2 Dockerfile — XR2 is untouched and still deployable at SHA `44cbdc81`.

Step 7 is the one I would not skip. It is the only way to see the whole
authorization path work before a document that could place an order exists.
