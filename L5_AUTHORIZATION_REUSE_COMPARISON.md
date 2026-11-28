# EXISTING / MISSING / ACTUALLY REQUIRED — authorization reuse comparison

No code written. Service **disarmed**. Two of my own earlier claims were
wrong and are corrected below.

## 1. The authorization mechanism prior successful campaigns actually used

Not a signature system. The campaigns that produced real submissions, fills,
cancellations, settlements and profitable completed pairs ran on:

| layer | mechanism |
|---|---|
| owner grant | **`L5_CAMPAIGN_APPROVAL`** — owner-set Railway variable, graded by `CA.ApprovalGate` against **code-constant ceilings** (`MAX_WINDOWS` 8, `MAX_TOTAL_EXPOSURE_USD` 20, `MAX_DAILY_LOSS_USD` 5, `MAX_CAMPAIGN_S` 4h, `ALLOWED_SERIES`) |
| owner arm token | **`PMUS_Q1_LIVE=YES` + `PMUS_Q1_SESSION_ID` + `PMUS_Q1_ARM`**, where `armed()` requires `ARM == SID` |
| absolute deadline | `L5_LIVE_AUTH_NOT_AFTER` |
| per-window grant | `authorize_arming()` → **single-use claim + scope digest**, windows spent durably |
| per-order grant | **`claim_submission`** / **`claim_accumulation_leg`**, `O_EXCL`, never auto-reset |
| signature path | **DORMANT.** `MANDATE_PUBKEYS = {}`, `mandate_enforcing: false` — *"the state every prior live campaign ran under"* |

**Correction to my last message.** I made an offline keypair "step zero".
That is wrong: it is a **second** authorization system, exactly what you
told me not to introduce, and no prior campaign used it. Withdrawn.

## 2. Correction: the multi-submission grant already exists

I claimed `claim_submission` is one-submission-per-authorization and that a
hybrid needs a new "sequence-grant" design. **That was wrong.** The money
path at `l5_live_execution.py:6966` already chooses between two claims:

```
claim_accumulation_leg(market, rnd, leg, payload, attempt, klass, reducing)
claim_submission(market, payload, klass, reducing)
```

`claim_accumulation_leg` is keyed on **(market, round, leg)** — N
submissions per campaign, already durable and never auto-reset. And the
class budgets are already in force and already graded, at the values I
tightened earlier this session:

| budget | value |
|---|---|
| `L5_LIVE_AUTH_MAX_OPEN` | **24** |
| `L5_LIVE_AUTH_MAX_COMPLETE` | **32** |
| `L5_LIVE_AUTH_MAX_REPLACE` | **16** |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | **72** (backstop, below the sum) |
| `reducing=` exemption | a risk-reducing order is not refused by the flat backstop |

So the existing campaign machinery **already expresses** "N orders, split by
OPEN / COMPLETE / REPLACE, with a total backstop and a reducing-order
exemption". That is precisely the grant a maker-open + defensive-complete
hybrid needs. **No new authorization system is required.**

## 3. Correction: the IntentGate is not on the money path

`CONTROLLER_INTENT_HAS_NO_AUTHORIZED_ADAPTER` — which I treated as *the*
blocker — is a refusal in a module the money never flows through:

- the Runner constructs `IntentGate` once (`:1021`) and calls **only
  `evaluate_prefix`** (`:2606`), a read-only evaluation
- **nothing in `src/` calls `IntentGate.admit()` or
  `submit_controller_intent()`**
- the real claim-gated send is inside `l5_live_execution.handle()`

So `ControllerLiveAuthorization`, the capability-object design, and the
no-construction test are all guarding a **parallel evaluator**, not the
executor. Building that capability would not have put a single order on the
venue. My scope expansion came from this error.

## 4. What is genuinely new for controller-generated orders

Almost nothing in authorization. The decision content is **already
row-free**:

- `probe_plan(market, authenticated, loss_ceiling)` → `TS.build_orders(bid,
  ask, depth…)` derives side, price and size from the **authenticated
  book**, not from any L5 field
- `defensive_execute(market, buy_side, held_side, resting, decide_kwargs,
  prov)` takes **no row at all** and already issues a mandate the submit
  path honours

The L5 row is the **trigger**, not the source of the decision.

### EXISTING / MISSING / ACTUALLY REQUIRED

| capability | status |
|---|---|
| owner campaign grant, graded against code ceilings | **EXISTING** |
| multi-order grant with OPEN/COMPLETE/REPLACE budgets | **EXISTING** |
| risk-reducing exemption from the flat backstop | **EXISTING** |
| per-window single-use claim + scope digest | **EXISTING** |
| idempotency (`controller_coid`, `already_submitted`, `DUPLICATE_SUPPRESSED`) | **EXISTING** |
| exact-ID cancellation + terminal proof (`cancel_confirmed`) | **EXISTING** |
| registry, watchdog, reconciliation, halt latch, loss halts | **EXISTING** |
| prospective residual cap, cash cap, 0.96 maker ceiling | **EXISTING** |
| opening decision from the book, row-free | **EXISTING** (`build_orders`) |
| defensive completion decision, row-free | **EXISTING** (`DEF.decide`, `alternatives`) |
| a **row-free trigger** for the opening path | **MISSING** |
| a **wired send** from the timer completion path | **MISSING** (`maybe_ts_completion` ends `"deliberately not wired"`) |
| provenance label distinguishing our order from L5's in tape/ledger | **MISSING** (small: a provenance field on the existing `prov` chain) |
| a signature/keypair system | **NOT REQUIRED** — withdrawn |
| `ControllerLiveAuthorization` capability | **NOT REQUIRED** for execution — it gates a read-only evaluator |
| a sequence-grant design | **NOT REQUIRED** — `claim_accumulation_leg` is it |
| `EXECUTION_MODE` ≠ MAKER_ONLY | **REQUIRED, and only for crossing.** Two code constants: `AUTH.EXECUTION_MODE` and `l5_mandate_signature.EXPECTED_EXECUTION_MODE`. Maker-only openings need neither. |

## 5. The smallest code change

Two parts, in strict order, and the first is independently useful:

**(a) Maker-only autonomous openings — no crossing, no new authorization.**
Extract the existing `TWO_SIDED_PROBE` branch of `handle()` into a row-free
method and call it from the runner's existing tick. Pure extract-method: no
new decision logic, no new limit, no new claim type, and it keeps
`claim_accumulation_leg`, the class budgets, the residual cap, the 0.96
ceiling and the whole `prov` chain exactly as they are. Add a provenance
field so our order is never indistinguishable from L5's.

**(b) Defensive crossing — only if (a) proves out.** Wire
`maybe_ts_completion`'s already-computed positive decision to the existing
row-free `defensive_execute`, and flip the two `EXECUTION_MODE` constants.
That is the only part that touches a hard safety control, and it is a
separate, separately-authorized change.

(a) alone makes autonomous openings possible under the **existing** mandate
machinery. (b) is what the hybrid adds.

## 6. Hard controls preserved under this plan

Nothing weakened: prospective residual cap 3, cash $12.00, worst-case
deficit $3.00, daily $5, L5 cumulative $20, account-wide $18.6325,
`NO_RECYCLING`, 0.96 maker ceiling, 0.20–0.60 opening band, halt latch,
watchdog, registry, reconciliation, `O_EXCL` claims never auto-reset,
idempotency. (b) adds the 1.00 all-in completion ceiling and the $2.00
defensive budget; the unsafe 1.20 default stays disabled.

## 7. Mandate status

`ac855ce3…` **EXPIRED 22:20:15Z** and will not be reused. Nothing deploys
or trades under it. A fresh owner-signed campaign approval is required
before (a) runs live — the same `L5_CAMPAIGN_APPROVAL` mechanism every
prior successful campaign used, with no second system.

## Standing caveat, unchanged

The economics still say autonomous opening loses money: ARGMAX **−$3.1400**
over 38 windows, negative in every opening-price band. (a) is worth building
because it is small and reuses everything; running it live is a decision
about whether to prove the **mechanism** at a measured negative expectancy.
