# Deployment plan and authorization request — c668cc3a + hardening

Nothing in this document is activated. The shadow lock is engaged, the
previous authorization is expired and exhausted, and no trading can occur
until the owner approves both parts below.

## Current state, verified from the running service

| fact | value | evidence |
|---|---|---|
| deployment | `3899770c` SUCCESS 04:54:41Z | Railway |
| running SHA | `239ce8d2` (branch `claude/l5-narrow-campaign-prep`) | deployment meta |
| shadow lock | **engaged** | `L5_LIVE_SHADOW_LOCK {"locked": true, "blocked": ["submit"], "cancel_still_permitted": true}` |
| arming | refused | `L5_LIVE_DISARMED {"why": "SHADOW_ONLY_LOCK"}` |
| absolute deadline | **EXPIRED**, `expires_in_s: -4732.4` | preflight row |
| campaign approval | `approval_stale`, `reserved_nothing: true` | preflight row |
| approval document | `796d4f9f…42af7ca` — preserved, not erased | preflight row |
| durable accounting | survived the restart (`largest_recorded_clip: 37.73`) | residual-ceiling row |
| requote | not deployed, and `L5_PRESEND_REQUOTE` unset | variable list |

Three independent refusals now stack: the shadow lock, the expired absolute
deadline, and the stale campaign approval. Cancellation remains permitted,
so reconciliation and the watchdog are unaffected.

## Safety verification did NOT pass for the requote — one blocker found

Reviewing `c668cc3a` against the money path found a defect that makes
`L5_PRESEND_REQUOTE=YES` a **trading-halting** change rather than a
throughput improvement.

`handle` captures `gated_money` — market, side, price, quantity, mode —
where the safety gates run, and compares it **byte-for-byte** with the
intent about to reach the wire. Its own comment states the rule: *"Any
later override must not change these behind the gates' back; the equality
check immediately before the wire enforces that."* A mismatch does two
things: refuses with `SAFETY_OVERLAY:INTENT_MUTATED_AFTER_GATES`, and calls
`store.mark_uncertain("intent mutated after the gates")`.

The requote changes `price` **after** that capture — measured in the source:
capture at offset 61656 of `handle`, the requote's mutation at 74650, the
check at 81176. So on the first crossing refusal the order would be
refused *and* the store left UNCERTAIN, which the arming and submission
gates themselves refuse on. On the measured 0015z window, enabling the
requote would have **stopped the engine trading**, not repriced one order.

None of the 55 requote tests saw this, because every one of them exercises
`presend_requote` in isolation and nothing exercised the call site. That is
the second time in this work that unit tests passed while the integration
was broken; the first was a `NameError` the method's own `except` would have
swallowed.

**Fix applied, and it needs its own review** because it touches a safety
boundary. The requote now *informs* that boundary instead of tripping it:
the single field it may move is moved explicitly, re-checked against the
gated value itself rather than trusted from the helper, and refused as
`SAFETY_OVERLAY:REQUOTE_NOT_BELOW_GATED_PRICE` if it is not strictly below.
Market, side, quantity and mode still have to match byte-for-byte, and the
provenance record carries the gated price it was checked against.

There is a larger alternative the owner may prefer: reprice **before** the
gates and let them validate the price actually sent, which needs no
exception at the integrity boundary at all. It is a materially bigger
change — the gate stack would re-run per tick against a fresh book — and it
is not what was asked for, so it is noted rather than built.

## Part A — the code, which is a deployment and not a campaign

Promote `c668cc3a` plus the hardening commit onto the deploy branch by the
established fast-forward + trigger-only commit + re-pin procedure, with
`L5_PRESEND_REQUOTE` **left unset** so the requote stays inert, and the
shadow lock **left engaged**. That puts the campaign-identity fix in place
and lets a boundary be observed with no money at risk.

Nothing about trading changes in Part A. It can be approved and watched on
its own, and that is the recommendation: the identity fix is the larger of
the two corrections and it is worth confirming its claim-ledger behaviour
on a live boundary before anything is armed.

### Deploy only while flat — a precondition, not a preference

Changing the claim namespace changes two keys at once, and they move
together by design: `claim_submission` binds its O_EXCL lock to
`"<experiment>|<market>"` and the cancel mechanism's `marker_identity`
reads the same id from the same place, *"so the two cannot disagree about
what 'already attempted' means."* Within a campaign the id is constant, so
restarts are safe.

What is not safe is deploying **mid-flight**. A cancel marker or an
accumulation claim written under the old unsuffixed id is invisible to the
new suffixed id, so a market already attempted would read as un-attempted
and a leg already claimed as unclaimed. The frozen round plans keyed on
`"<experiment>|<market>|<round>"` would likewise be re-planned.

The current state is the right moment, and it is why this should go out
now rather than later: shadow locked, approval expired, windows 4 of 4
spent, residual flat, nothing live. The deployment must confirm all of that
immediately before promoting, and abort if anything is live or unresolved.

### One blocker to resolve first, found while verifying

`L5_EFFECTIVE_RUNTIME_CONFIG` currently reports **`PASS: false`** with
eight failing rows, because the verifier's expected profile was never
updated when the owner approved the larger budgets:

| row | effective | expected by verifier |
|---|---|---|
| `submission_backstop` | 40 | 56 |
| `submission_budget_open` | 40 | 16 |
| `submission_budget_complete` | 40 | 24 |
| `submission_budget_replace` | 64 | 32 |
| `aggregate_exposure_usd` | 20 | 5 |
| `aggregate_exposure_in_force_usd` | 20 | 5 |
| `opening_notional_budget_usd` | 3.00 | 0.80 |
| `max_windows_authorization` | 4 | 8 |

Every "effective" value is the one the owner approved; every "expected" one
is stale. This is not a safety failure, but a config gate that always reads
FAIL is a gate nobody can use, and it would make the pre-arm checklist
meaningless. It should be reconciled to the approved figures **before** any
arming, as a separate narrow change. I have not touched it: changing what a
safety verifier expects is not something to do quietly alongside other work.

## Part B — the campaign, requested only after Part A is observed clean

Four windows, **every capital and loss limit unchanged**:

| limit | value |
|---|---|
| eligible windows | 4 |
| max total exposure | $20 |
| max cash deployed | $12 |
| max daily loss | $5 |
| cumulative loss cap | $20 |
| worst-case deficit | $3 |
| round allocations / max rounds | 4 / 8 |
| simultaneous resting orders | 1 |
| maker-only | yes |
| two-order exception | OFF |
| OPEN / COMPLETE / REPLACE | 40 / 40 / 64 |
| series | `cpc-btc-updown-15m-` |

The only behavioural change is `L5_PRESEND_REQUOTE=YES`, and it should be
approved only after the integrity-boundary fix above has been reviewed on
its own terms. No new service, no
infrastructure change, no loosened control, and no reset of loss
accounting: the cumulative-loss ledger, the obligation ledger and venue
reconciliation all carry forward.

### What it must measure, and what would make it a failure

Primary: **completed pairs per window**, with each pair's cost. Secondary:
stranded quantity and its settlement. Required: **requoted fills accounted
separately from unrequoted ones**, each with its `ticks_lower` and eventual
settlement, because the replay shows the requote's one profitable pair was
conditional on a rebound. Without that split the result cannot distinguish
a throughput gain from a cost-basis illusion.

A campaign that raises order count while stranded settlement worsens is a
**negative** result and should stop the requote, not justify more windows.

### Why the authorization document is not minted yet

`CA.MAX_CAMPAIGN_S` caps a campaign at four hours and the gate clamps the
document's effective expiry to it, so a document minted now would very
likely be `approval_stale` before it was ever used — which is exactly what
happened to `796d4f9f…`: it expired at 03:35:54Z with its four windows
already spent, and the engine has been refusing on
`CAMPAIGN_IDENTITY_UNAVAILABLE` ever since.

So the scope above is what I am asking to be approved, and the document is
minted at arming time once the owner says go. Its fingerprint and expiry
are reported back for verification before the lock is lifted, not before.

### Pre-arm checklist

1. deployed SHA is exactly the promoted commit, and the runtime reports it;
2. `L5_EFFECTIVE_RUNTIME_CONFIG` reads `PASS: true` (needs the Part A
   blocker resolved);
3. `L5_PRESEND_REQUOTE` reads YES and the config verifier confirms it;
4. approval fingerprint, expiry and nonce verified against the owner's
   message;
5. **the submission-claim ledger under the new `experiment_id` is read and
   shown empty** — the fix makes this automatic, which is exactly why it is
   verified rather than assumed;
6. campaign window ledger shows 0 of 4 for the new nonce;
7. prior obligations resolved, residual flat, no unresolved accounting;
8. watchdog, cancellation, reconciliation and halt latch confirmed live;
9. `L5_SHADOW_ONLY` lifted **last**, only after 1–8 pass.

Any failure keeps the lock engaged and is reported as the specific blocker.
No check is bypassed, and no automatic relaunch after completion or a crash.
