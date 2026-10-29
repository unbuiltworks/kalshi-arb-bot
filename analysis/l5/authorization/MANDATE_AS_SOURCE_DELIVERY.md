# Mandate as an authorization source — implementation delivery

**Code and tests only. Nothing is deployed, no key is registered, no
mandate is signed, and the new mode is OFF.** With the flag unset the
system behaves exactly as it did for campaigns 1–9.

## 1. What changed

Four files. `git diff` for the two modified ones; the other two are new.

| file | change |
|---|---|
| `src/l5_mandate_signature.py` | `+49` — one new function, `identity_unverified()` |
| `src/l5_live_authorization.py` | the opt-in flag, three new functions, the three-way disjunction |
| `tests/test_l5_mandate_as_authorization.py` | new, 52 tests |
| `tools/make_mandate_payload.py` | new, builds and validates the unsigned 7-day payload |

### The opt-in

```python
MANDATE_AS_AUTH_ENV = "L5_MANDATE_AS_AUTHORIZATION"

def mandate_as_authorization_enabled() -> bool:
    raw = (os.getenv(MANDATE_AS_AUTH_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")
```

Unset, blank, `0`, `false`, a typo — all OFF.

### The disjunction, before and after

Before, arming required **one of** {campaign document, legacy Q1 handshake},
**and additionally** a valid mandate whenever `MANDATE_PUBKEYS` was
non-empty. After:

```
if   campaign valid              -> campaign path   (UNCHANGED)
elif flag set and mandate present -> mandate path    (NEW)
elif Q1 handshake armed           -> legacy path     (UNCHANGED)
else                              -> refuse, naming all three
```

### The property that makes this safe to merge

**With a valid campaign document the mandate stays required, flag or not.**
The new branch is reachable only where the old code already refused
`OWNER_AUTHORIZATION_ABSENT`. Enabling the flag therefore cannot relax any
path that works today; it can only add one where there was a refusal.

The one behavioural nuance: a *failed* mandate no longer refuses on the spot
**iff** the flag is set and there is no valid campaign. It refuses two
statements later with a better name (`MANDATE_EXPIRED`,
`MANDATE_REVOKED`, …) instead of before step 2 has established that nothing
else could have authorized.

### Per-day budgets ride the existing counters

`mandate_claim_sid()` is `mand.<sha256(mandate_id#seq|key_id)[:16]>.<YYYYMMDD>`.

Putting the UTC date inside the claim identity turns the **existing**
`windows_spent()` and `submissions_spent()` prefix counters into per-day
counters. No new storage, no new counter, no second writer of any budget.

`mandate_claim_suffix()` is a pure function of `L5_MANDATE_DOC` and the
clock, mirroring `campaign_claim_suffix()`, so a restart re-derives the same
identity and counts the same claim files — **budget replenishment stays an
owner action** (a new `seq`, or tomorrow). It deliberately does **not**
verify: verification needs the watchdog, so a verifying namespace would do
network I/O per claim and hand out a *fresh* budget exactly when the system
is least healthy. Getting the namespace wrong can only mis-count; it can
never authorize. Anything unreadable yields `""`, which falls back to the
bare experiment id and counts **more** claims, never fewer.

### Limits can only tighten

`mandate_window_rate(scope, caller_max)` → `min(signed, caller)`, a named
function so there is one place to read the rule and one to test it. An
unreadable rate **refuses** rather than defaulting; a bool is not an int,
because `True` is `1` to Python and "one window a day" is not what `true`
meant.

The signed submission rate binds through the same intersection discipline
the exposure and daily-loss ceilings already use: arming refuses if the
deployed backstop is **absent** (`MANDATE_SUBMISSION_BACKSTOP_ABSENT`) or
**wider than signed** (`MANDATE_SUBMISSION_BACKSTOP_WIDER_THAN_SIGNED`).
See §5 for why it is not enforced directly.

New refusal reasons, all fail-closed:

```
MANDATE_IDENTITY_UNKNOWN
MANDATE_WINDOW_RATE_UNREADABLE
MANDATE_SUBMISSION_RATE_UNREADABLE
MANDATE_SUBMISSION_BACKSTOP_ABSENT
MANDATE_SUBMISSION_BACKSTOP_WIDER_THAN_SIGNED
```

The existing `needs_either` key in the final refusal keeps its name even
though there are now three routes — it is a published shape an operator
reads and a test pins, and renaming a contract to fix a word is churn.

## 2. What is preserved

Unchanged and still enforced on the mandate path:

- every dollar limit, via `ValidationGates` and the campaign budget ledger
- the halt latch as the fast withdrawal path — the gate **requires**
  `L5_HALT_URL` and refuses `MANDATE_REVOCATION_UNAVAILABLE` without it;
  this process cannot clear the latch
- the replay floor and revocation list, GET-only from the watchdog, both
  fail-closed on absence, staleness or an unreachable host
- `seq` strict monotonicity, so a rollback to a looser document refuses
- unknown field refuses; money must be a string; timestamps need an offset;
  `MAKER_ONLY` re-asserted; `requires_bounded_live_validation` must be true
- durable O_EXCL window and submission claims, exact-order cancellation,
  and the reconciliation path — untouched
- `l5_campaign_approval` — untouched

## 3. Tests

`tests/test_l5_mandate_as_authorization.py`, **52 tests**, covering each
scenario requested:

| requirement | tests |
|---|---|
| default off | flag parsing, truthy-only, registry still empty |
| baseline parity | valid mandate does **not** authorize with flag off; `experiment_id` unchanged |
| restart | same document, same UTC day, different moment → identical suffix |
| duplicate claims | 50 derivations → one identity; concurrent arms contend for one O_EXCL name |
| day rollover | 23:59 vs 00:01 → different namespace; new `seq` and rotated key also separate |
| expired mandate | refuses by its own name, not "absent" |
| stale watchdog | floor and revocation unavailable, store URL unset, halt latch absent — all fail closed **with the flag on** |
| revoked mandate | revocation list and rolled-back `seq` |
| changed scope | running limits wider than signed; tampered payload → `MANDATE_SIGNATURE_INVALID`; non-MAKER |
| never overrides stricter | `min()` both directions; unreadable rate refuses; gate routes through the one helper |
| campaign preserved | invalid mandate still blocks a valid campaign; campaign wins the namespace |
| private key absent | no runtime module imports the signer; mandate module can verify but cannot sign; registered keys are public halves only; telemetry carries no document |

Run:

```
python3 -m pytest tests/test_l5_mandate_as_authorization.py -q    # 52 passed
python3 -m pytest tests/ -q -k "mandate or authorization or campaign or claim or arming or daily"
                                                                   # 1408 passed, 1 skipped
```

## 4. The seven-day configuration, prepared and not activated

`tools/make_mandate_payload.py` emits the unsigned payload and runs the real
verifier's own field checks over it, with the clock set an hour after
`not_before` so "not yet valid" is not reported as a defect.

```
validates_against_real_verifier: true    problems: []    signed: false
window : 2026-10-30T00:00:00+00:00 -> 2026-11-06T00:00:00+00:00
rates  : 8 windows/day, 72 submissions/day
capital: cash $12, exposure $20, campaign budget $29.30
losses : daily halt $5, cumulative $20
mode   : MAKER_ONLY | series cpc-btc-updown-15m- | residual 3
```

Every number is the envelope already approved for campaigns 8 and 9. The
document changes the **duration** and the **shape** of the budget (per-day
rather than per-campaign), never a limit. Note that 8 windows and 72
submissions as a per-*day* rate is a **reduction** against any day that
would otherwise have run two 4-hour campaigns.

## 5. Remaining risks, including one I would fix before activation

**(a) The signed submission rate is bounded, not directly enforced.**
`max_submissions_per_day` is made binding by refusing when the deployed
backstop is wider. It is not itself the counter. I chose this because the
enforced budget already exists and has one writer, and two writers of one
budget is how a budget gets spent twice. The cost: the owner's signed number
takes effect through a second variable, and a deployment that lowers the
backstop silently tightens below what was signed (safe direction, but not
what the document says).

**(b) `src/l5_daily_action_ledger.py` already exists and is wired into
nothing.** It is purpose-built for exactly these two per-day rates, with
idempotent re-arm (`reserve_window` resumes rather than respends on a
restart), O_EXCL claims and `DailyLedgerUnavailable` raising rather than
defaulting. It is the better long-run home for (a). I did **not** wire it
here because `reserve_window` *writes*, and placing a new write inside the
arming sequence risks spending a window on an arm that later fails preflight
— the module's own `would_reserve_window` exists because of that hazard.
**Recommendation: wire it as a follow-up change, before the rates matter at
scale.** It needs its own tests for the write-placement question.

**(c) Standing authority goes from 4 hours to 7 days.** Mitigated by the
per-day rates, unchanged dollar ceilings, the halt latch, the revocation
list and `seq` monotonicity — but it is a real increase and the headline
risk of the whole design.

**(d) Ordering is load-bearing.** Registering a public key makes the mandate
gate REQUIRED immediately. This code must be deployed *before* a key exists,
or live arming stops until a signed mandate is installed.

**(e) Watchdog liveness becomes a trading dependency.** A stale served
document correctly refuses, so a dead watchdog stops trading. That is the
right failure direction and a new operational coupling.

**(f) Untested in production.** 52 unit tests and a clean full-suite run are
not a live arming. Nothing here has authorized a real order.

## 6. Minimum unavoidable owner setup

Two steps cannot be delegated, because they are the security property:

1. **Generate the keypair offline** — `docs/OWNER_MANDATE_SIGNING.md` step 1,
   one command. The private half must never enter this repo, Railway, a log
   or a chat.
2. **Hand over the 64-hex PUBLIC key** for a reviewed commit to
   `MANDATE_PUBKEYS`. A repo commit is deliberately the only way to arm the
   gate: *"no variable edit can satisfy it."*

Then, per mandate (about weekly at first):

3. Sign the payload from §4 with `tools/owner_sign_mandate.py`. It refuses a
   key inside the repo or a group-readable key file, and dry-runs the real
   verifier.
4. **Install it** — and this one *is* delegable. A signed mandate is not a
   secret; its authority is the signature, so it can be pasted anywhere and
   I can install it through the Railway connector. Writing it is
   insufficient without the key.

## 7. Activation order, when approved

Each step is separately reversible and nothing arms differently until the
last two.

1. Merge this change. Gate inert (no keys), flag off. **No behaviour change.**
2. Verify the deployed watchdog actually serves `mandate_floor.json` and
   `mandate_revocations.json`, via the existing `L5_MANDATE_STORE_PROBE`.
   Do this **before** a key exists, not after.
3. Owner generates the keypair and supplies the public half.
4. Commit the key. **The gate becomes mandatory at this point** — a signed
   mandate must be installed in the same change window.
5. Sign and install the 7-day mandate.
6. Set `L5_MANDATE_AS_AUTHORIZATION=1`. Only now does the mandate authorize
   on its own.
7. First window under the mandate: verify `budget_release`, the claim
   identity, the per-day counters and the halt path before leaving it
   unattended.
