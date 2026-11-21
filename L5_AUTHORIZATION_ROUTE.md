# Is the signature necessary? No. Fastest safe route to live trading.

**Short answer: I was wrong to call your public key the remaining blocker.
The signature gate is dormant and is not blocking anything. The previous
mechanism works, unchanged, and enforces every property you listed. The
smallest safe change is ZERO CODE — three owner settings.**

---

## 1. The correction

I told you the public key was the remaining blocker. That was wrong, and here
is the proof rather than the assertion. The gate I added is guarded by:

```python
from src import l5_mandate_signature as MS
if MS.MANDATE_PUBKEYS:          # empty -> the whole block is skipped
```

`MANDATE_PUBKEYS` ships **empty**, so step 1b never runs. Production telemetry
agrees: `mandate_enforcing: false`, `mandate_trusted_key_ids: (none)`.

Two new tests pin it. With no key, no mandate document, no store URL and no
halt gate, `authorize_arming` refuses **`OWNER_AUTHORIZATION_ABSENT`** — the
*pre-existing* requirement — and no `MANDATE_*` reason is reachable. A second
test walks the ordinary refusal paths and asserts the named reason is
unchanged from before the gate existed.

**This is not "silently disabling" a gate.** Empty is its shipped, documented
default; it is reported in the heartbeat on every boot; and it is the state
every prior live campaign ran under. Nothing was switched off to get here.

---

## 2. What the existing mechanism already enforces — independently

You asked whether it covers scope, expiry, capital, loss, cancellation,
revocation and watchdog halts. It covers all seven, and none of them depends
on a signature.

| property | enforced by | why the document cannot widen it |
|---|---|---|
| **Scope** | `ALLOWED_SERIES` is a **code constant**; a series outside it refuses | the allowlist is in code, not in the document |
| **Expiry** | three independent clocks: `approval_stale` at 3600 s · `eff_expires = min(expires, issued + MAX_CAMPAIGN_S)` (4 h ceiling) · `L5_LIVE_AUTH_NOT_AFTER` absolute deadline | a document asking for a week gets four hours |
| **Capital** | `MAX_TOTAL_EXPOSURE_USD` $20 code ceiling, intersected with the ask; `max_cash_deployed_usd` $5; **`CAMPAIGN_LIMITS_WIDER_THAN_APPROVED`** refuses if running limits exceed the approved scope | a document asking $50 yields $5 |
| **Loss** | `DAILY_REALIZED_LOSS_HALT` on the UTC-day figure; `max_cumulative_loss_usd` $20 account-wide; `$2.00` worst-case deficit per order | code constants |
| **Cancellation** | exact-order cancellation by venue id, plus the **independent watchdog canceller** | separate service, cancel-only transport |
| **Revocation** | the **halt latch**. `HaltAsserter` is *"write-only, assert-only. Has no delete and no clear, by construction"* and is held by the watchdog | the executor has **no code path** to clear it |
| **Watchdog halts** | `HaltGate` over HTTP, every failure reading as *not permitted* | fails closed; unreachable ⇒ refuses |
| **Single use** | the approval's nonce mints a **permanent single-use claim**; a spent nonce refuses | replay is refused |

Every one of these is a **code constant or an independent service**. The
approval document is the *intersection* of what it asks for and what the code
permits — it can only ever narrow.

## 3. So what does the signature actually add?

Exactly one thing: it authenticates **who** approved. Nothing else.

The honest statement of the gap, once: the approval lives in a Railway
variable, and I hold a Railway token, so the system cannot by itself
distinguish your approval from one I wrote. That has been true for every prior
live campaign, including the ones you authorized. It is a real property, it is
your account and your money, and it is your risk to accept.

**It is not an enforcement gap.** Every limit above binds regardless of who
wrote the document.

## 4. A no-key way to close that gap anyway

You can verify the approval in force is **yours**, with one command and no
keypair.

`L5_AUTHORIZATION_PREFLIGHT` already reports `document_sha256`, which is
`sha256` of the **raw variable bytes**. So it is reproducible locally:

```bash
printf '%s' '<the exact JSON you pasted>' | sha256sum
```

Compare that to `document_sha256` in the preflight line. If the digest matches,
the document in force is byte-for-byte the one you wrote. **If I had written a
different one, the digest would not match the one you computed.** Verified
reproducible: the store returns `str(raw).encode("utf-8")` and the digest is a
plain `hashlib.sha256` over those bytes.

That gives you the authentication property for the cost of one `sha256sum`,
and it requires nothing from me.

---

## 5. The smallest safe change: zero code

Nothing needs building, deploying or disabling. Three owner settings, all on
`l5-pmus-live`:

### Step 1 — publish the campaign approval

Set **`L5_CAMPAIGN_APPROVAL`** to this document. Required fields exactly,
money as **strings**, timestamps **offset-aware**:

```json
{
  "approved": true,
  "nonce": "l5-twosided-20261024-<something unique>",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<now, offset-aware>",
  "expires_utc": "<now + up to 4h, offset-aware>",
  "max_windows": 8,
  "max_total_exposure_usd": "5",
  "max_daily_loss_usd": "5"
}
```

It goes **stale after 3600 s**, so publish it when you intend to start.
**Please set it yourself** — then the one in force is demonstrably yours, and
I will not write it.

### Step 2 — the absolute deadline

Set **`L5_LIVE_AUTH_NOT_AFTER`** to the campaign end (offset-aware). It is
currently **EXPIRED**, which is the other thing stopping arming.

### Step 3 — the four behaviour knobs

| variable | from | to | effect |
|---|---|---|---|
| `L5_TS_TWO_ORDER_PAIR` | off | `1` | both legs may rest at once |
| `L5_TS_ROUND_ALLOCATIONS` | 1 | `2` | two inventory-aware rounds per window |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | 3 | `2` | keeps a single-side fill off the residual cap |
| `L5_TS_MIN_FIRST_LEG_PRICE` | **0** | `0.20` | restores the price floor (currently not in force) |

Already set, and verified in-container: `L5_LIVE_AUTH_MAX_OPEN=16`
(`effective: 16, ok: true`), `L5_MAX_WORST_CASE_DEFICIT_USD=2.00`,
`L5_MANDATE_STORE_URL`.

### Then

Arming becomes possible and I run the campaign inside the limits already
approved: **$5 aggregate exposure, $2.00 worst-case deficit, 16 OPEN
submissions, 2 contracts per leg, maker-only, ≤8 windows and ≤4 hours.**

---

## 6. What I am keeping, and what I am not doing

- **Keeping** the signature gate, dormant. It costs nothing while
  `MANDATE_PUBKEYS` is empty, it is reported on every boot, and it is there if
  you ever want it. Removing it would be deleting a control rather than
  declining to use one.
- **Keeping** the watchdog publishing fix — it is deployed and verified
  (`BOTH_DOCUMENTS_FRESH`), and it is a prerequisite only for the signature
  path, so it sits harmlessly until then.
- **Not** bypassing authorization: the campaign path is the authorization, and
  it refuses on its own terms.
- **Not** weakening any risk control. Everything in this route is an existing
  limit at or below its approved value.
- **Not** writing `L5_CAMPAIGN_APPROVAL` myself.

## 7. Still outstanding, unchanged by this route

- **The campaign ledger bound.** `L5_LIVE_GATES` shows the open Stage 1 ledger
  at `budget_usd 29.30, remaining 12.6790`. My $5.00 re-scope of the constant
  applies only to a *new* ledger, so the campaign-wide worst case is bounded
  at **$8.3787** by the account-wide cumulative gate, not at $5.00. The $5
  **instantaneous** ceiling is enforced.
- **49 legs with no cost basis**, which degrades realized-P&L measurement
  without blocking arming.

## Accounting labels

Venue-confirmed realized to date: **+$0.1336** (2026-10-23, 8 windows,
sequential two-sided) and **−$3.7194** (Stage 1, 8 windows). At $5 the campaign
cannot measure the edge (signal ÷ residual noise ≈ 0.087); its honest purpose
is mechanical validation of continuous L5-style execution.
