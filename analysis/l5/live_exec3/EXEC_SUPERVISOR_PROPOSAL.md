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
