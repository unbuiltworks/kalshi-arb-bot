# Controller → live executor: the exact seam, and why it is not a small diff

No code written this turn. Service **disarmed**. Below is what each
interface actually requires, read from source, and the three decisions that
are yours rather than mine.

## Headline: the expected execution economics are NEGATIVE

Before any of the mechanics, the thing a bounded live validation would be
validating:

| measured | value |
|---|---|
| autonomous ARGMAX, 38 settled windows | **−$3.1400** (−$0.0826/window) |
| every opening-price band | **net negative** (−0.0011 to −0.4000/window) |
| matched : residual, needed vs actual | **4.14 : 1** needed, **3.17 : 1** actual |
| defensive completion rate needed to break even | **26.8%** |
| that rate's evidence | **n = 1** (0130z, completable at pair 0.9656) |

A three-window live run of this policy is, on our own measurements, a
proposal to lose money slowly with a plausible path to losing it faster.
That is the honest expected economics, and it is why I am not returning a
mandate.

## What the seam already gives us

`prepare_controller_submission` runs the full 12-gate chain, builds
`controller_coid`, checks `already_submitted`, verifies the store API, and
**returns the wire payload**. `submit_controller_intent` then stops at
`CONTROLLER_SUBMISSION_HAS_NO_CLAIM_GATED_ROUTE`. That part is genuinely
small and genuinely ready.

## Why the rest is not small — four structural findings

### 1. `claim_submission` is ONE submission per authorization

`AUTH.claim_submission(market, payload, path=…)` takes an
`O_CREAT|O_EXCL` single-use lock scoped to (experiment, market).
`tests/test_l5_one_submission_claim.py` proves it is **not released** by:
a cancellation · a partial fill · a zero fill · a restart · a rejection ·
an ambiguous acknowledgement.

**So a hybrid that OPENS and then DEFENSIVELY COMPLETES needs two claims.**
`ControllerLiveAuthorization` bounds one market and one `max_size` and has
**no count field** — it cannot express "a bounded sequence of N orders".
Granting the hybrid therefore means designing a sequence-grant, which is a
risk decision about how many orders one signature may authorise. That is
yours, not a detail I should pick.

### 2. The capability is constructible in exactly one module

`tests/test_l5_intent_gate.py` scans every `.py` under `src/` except
`l5_intent_gate.py` and fails if `ControllerLiveAuthorization(` appears.
A second test asserts `l5_intent_gate` contains **no `os.getenv` and no
`environ`**.

Those two together are requirement 7 already enforced: the authorization
cannot be an environment variable, and cannot be minted anywhere else. The
only compliant shape is a factory **inside** `l5_intent_gate.py` taking an
already-verified mandate as an argument. Both tests must then be edited
deliberately — they are the designed control points, not obstacles.

### 3. Requirement 7's validator exists, and step zero is yours

`l5_mandate_signature.verify()` already does everything requirement 7 asks:
signature against a public key held **in code**, binding to environment,
service role and series, a watchdog-published **revocation set** and
**replay floor** where `None` means *not established and refuses*, a
30-day ceiling, and no I/O.

Its shipped state is `MANDATE_PUBKEYS = {}` → **`MANDATE_NO_TRUSTED_KEY`,
refusing every mandate.** Populating it needs a keypair whose private half
is generated offline and must never exist in this repository, in Railway,
or anywhere the executor can read — there is a **graded** verifier row,
`mandate_signing_key_absent_from_runtime`, enforcing exactly that.

**I cannot create that key, and should not.** It is the one step that must
start with you.

### 4. Defensive crossing needs two code constants changed, not a document

- `AUTH.EXECUTION_MODE = "MAKER_ONLY"` (code constant)
- `l5_mandate_signature.EXPECTED_EXECUTION_MODE = "MAKER_ONLY"` — so the
  signature path **also** refuses a crossing mandate

Both are deliberately in code so no document and no variable can grant
crossing. Confirmed earlier: `execution_scope` is not in
`CA.REQUIRED_FIELDS` and would be silently ignored.

## What I did not do, and why

I did not write `src/l5_controller_submit.py` this turn. Writing a send
that spends a claim, records before the POST, attaches the venue id, and
routes an ambiguous acknowledgement to `mark_uncertain` is five interfaces
deep in the money path. Doing that well is a focused change; doing it
hastily, for a policy measured at −$3.14, against an authorization model
that cannot yet express the grant it needs, and behind a public key that
does not exist, would be the wrong order of operations.

The four locks all remain engaged: no keypair, `MAKER_ONLY` in two places,
no capability constructed, service disarmed on three preflight counts.

## The three decisions that unblock this

1. **Generate the owner keypair offline** and give me only the **public**
   half for `MANDATE_PUBKEYS`. Nothing else in this list can proceed first.
2. **Decide the sequence-grant.** How many submissions may one signed
   mandate authorise — 1 (open only, complete never), 2 (open + one
   completion), or N bounded? This sets whether the hybrid is expressible
   at all.
3. **Accept or reject the economics.** On present evidence autonomous
   opening loses money. If you want live submissions regardless — to prove
   the *mechanism* rather than the edge — say so explicitly and I will
   build it to the smallest size that can produce a confirmed fill, and
   report it as a mechanical validation, not as profitability.

Given (3), my recommendation is still the one the data supports: the
defensive-completion result (+1.1199 at λ=0.5, +1.1492 at λ=8.818 against a
+0.1256 baseline) is the only positive finding, and it attaches to
inventory that something must first open. Autonomous opening is what we
cannot yet make pay.

## Proposed bounded live validation — held, not returned

Unchanged in shape and ready when (1)–(3) are settled: 3 windows, **$20**
exposure, **$5** daily loss, **$3** worst-case per window, ceiling
**1.00**, defensive budget **$2.00**, maker openings plus authorized
defensive completion, halt after the first crossing, every other limit
unchanged and upstream. I am not submitting it for signature while the
expected economics are negative and the grant cannot be expressed.
