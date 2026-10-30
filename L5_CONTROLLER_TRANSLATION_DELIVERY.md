# Route (c): the row-free controller translation, wired to the live path

Autonomous maker openings are now **technically executable** through the
existing PMUS infrastructure. Nothing is armed, nothing is deployed, and
nothing here says the strategy is profitable.

### Production state, and what of it I actually re-read

Verified this turn, by listing `l5-pmus-live`'s variables in
`arb-ev-wallet-shadow/production`: **`L5_CONTROLLER_OPEN` is not set on the
service**, so the path this commit adds is off in production and cannot
make a decision. `L5_DEFENSIVE_COMPLETION` and `L5_BOOK_COMPLETION` exist
as before; no variable was added, changed or removed, and nothing was
deployed.

**CORRECTED 2026-10-30 from the service's own logs.** The claim above that
preflight was `ok: false` on three counts was stale. The running service
reports `L5_LIVE_PREFLIGHT: PASSED` with `armed_after_preflight: false`,
`executor_starts_disarmed` passed and `no_unexpected_open_orders: 0`; the
only failed check is `owner_arm_token_present`, which is explicitly
non-blocking ("arming is a separate owner act"). The authorization blocker
is a single one: `L5_EFFECTIVE_RUNTIME_CONFIG` fails eight rows, every one
reading `required: NO_APPROVAL_IN_FORCE` — there is no campaign approval in
force. The deployed figures are already the authorized ones. See
`L5_CAMPAIGN10_LIVE_VALIDATION_PROPOSAL.md`. `EXECUTION_MODE = MAKER_ONLY`, defensive crossing off
and the 1.00 pair ceiling are properties of the code in this commit and are
asserted by its tests.

## What was actually blocking it

`FT.pmus_translate(decision, row, authenticated, want)` read three things
out of an L5 activity row — the book, the staleness age, and the
maker-class key. The three gates behind them (`STALE_MAX_S = 2.0`, book
availability, maker-class strictness) were therefore unreachable without a
row, and the three ways out each broke a stated requirement. Route (c) is
the one that breaks none: extract the body, share it, re-pin the freeze
with parity evidence.

## The nine requirements

| # | Requirement | Where |
|---|---|---|
| 1 | L5 behaviour preserved exactly; no fabricated rows or action keys | `_translate_core`; 21-case parity suite; `controller_translate` contains no `row`, `action_key`, `pmus` or `authenticated` reference, asserted on its AST |
| 2 | One shared implementation of all three checks | `_translate_core` is the only copy; both entry points are asserted to contain no `STALE_MAX_S`, `T_NO_BOOK` or `STRICT_CLASSES` of their own |
| 3 | Explicit autonomous provenance throughout | `Provenance(provenance=…)`, `rec["provenance"]`, `decision_source`, `CONTROLLER_BOOK` note, and a provenance-split `MAKER_TREATMENT` record |
| 4 | Freeze updated deliberately, with parity evidence | three independent guards re-pinned, reason recorded in each |
| 5 | Connected to the trigger, `claim_accumulation_leg`, and the live submission path | `Runner.maybe_controller_open` → `handle(None, book, controller=…)` → two-sided branch → claim → transport |
| 6 | Every existing control preserved | `handle` unchanged after the four touchpoints; 45th refusal site added to the reviewed inventory |
| 7 | Six scenario categories tested | `tests/test_l5_controller_open_trigger.py`, 47 tests |
| 8 | `MAKER_ONLY`, crossing disabled, not armed, not deployed | asserted on the wire body across rounds and both sides |
| 9 | Diff, tests, parity evidence, blockers | below |

## The production-bound diff

All six changed modules are in the `l5_pmus_live` Dockerfile COPY list
except `l5_session_integrity.py`, which that service does not import. **No
new `src` module was added**, so no Dockerfile change is required.

```
src/l5_faithful_translation.py     |  80 ++   the extraction + controller_translate
src/l5_live_execution.py           | 238 ++   provenance, helpers, handle(controller=)
src/l5_pmus_live_runner.py         | 137 ++   maybe_controller_open + tick dispatch
src/l5_session_integrity.py        |  20 ++   RUNTIME freeze re-pin
src/l5_runtime_config_verify.py    |  12 ++   the new flag, reported at boot
src/l5_intent_gate.py              |   6 ++   the 45th refusal site
tests/…                            |  72 ++   three guards re-pinned
tests/test_l5_controller_translate_parity.py  NEW  51 tests
tests/test_l5_controller_open_trigger.py      NEW  47 tests
```

### How a decision now reaches the wire without L5

```
timer tick
  → Runner.maybe_controller_open            L5_CONTROLLER_OPEN off by default
      TS.active(market)                     the probe engine must be authorized
      still_fillable_order_count() == 0     one outstanding order
      book.snapshot(slug)                   authenticated WS, two-sided or None
      provisional side = cheaper own price  superseded below
  → executor.handle(None, book, controller={provenance, decision, maker_class})
      Provenance(provenance=CONTROLLER_GENERATED)
      FT.controller_translate(...)          the SAME three gates
      → the two-sided branch                build_orders prices, sizes, picks the leg
      → key = CONTROLLER|OPEN|<mkt>|r<n>|<LEG>     resolved here, fail-closed
      → client_order_id(key)                → DUPLICATE_SUPPRESSED on a repeat
      → every existing gate, unchanged
      → claim_accumulation_leg(market, round, leg)
      → transport.place(post-only MAKER)
```

Measured end to end on the real executor: no row, key
`CONTROLLER|OPEN|m|r0|DOWN`, a post-only MAKER order at `0.37` for `3` —
`build_orders`' own price, not the provisional `0.39`, which is the proof
that the branch still owns the order.

## Parity evidence (requirement 4)

`tests/test_l5_controller_translate_parity.py` loads the **pre-refactor
module from git** as a separate module object and calls its
`pmus_translate` beside the current one on identical inputs. 21 cases, each
asserted field for field, then all 21 asserted again through the row-free
entry point:

- missing book — both sides, and bid-only / ask-only
- stale snapshot — `2.01`, `30.0`, and the boundary `2.0` and `None`
- stale **and** bookless, which pins the gate **order**
- maker class — absent, `None`, `NO_INTERACTION`, ambiguous, bare string, and a real `STRICT_CLASSES` member
- valid MAKER, valid TAKER, zero depth, no depth, and a depth-capped TAKER
- UP and DOWN

**Zero behavioural differences.** The matrix is itself asserted to cover
both `ok` outcomes, both modes and ≥5 distinct refusal reasons, so it
cannot pass by only ever hitting one branch.

Three independent guards were re-pinned to `dda950a57331cba8`, each
carrying the reason:

1. `tests/test_faithful_rule_frozen.FROZEN` — the authority
2. `src/l5_session_integrity.FROZEN` — a **runtime halt**, not a test
3. `tests/test_l5_live_runner_graft` — byte identity, replaced by identity to the one recorded hash

## Test results

| Suite | Result |
|---|---|
| `test_l5_controller_translate_parity.py` | 51 passed |
| `test_l5_controller_open_trigger.py` | 47 passed |
| `test_faithful_rule_frozen.py` | passed |
| `test_l5_one_submission_claim.py` | passed (claim-site scan) |
| `test_l5_intent_gate.py` | 85 passed |
| full regression | 14,243 passed / 54 failed / 26 errors — **identical to the pristine baseline** bar three `httpx` collection errors reproduced with the change stashed |

## Remaining blockers

1. **The economics are unchanged and still negative.** ARGMAX was −$3.1400
   over 38 windows and every opening-price band was net-negative. This
   work removed an *executability* blocker, not an *expectancy* one. No
   live campaign is justified by it.
2. **The three-service freeze coupling.** `pmus_ws_recorder` (the 24/7
   read-only collector) and `l5_live_probe` both carry
   `l5_faithful_translation.py` **and** `l5_session_integrity.py`. The
   re-pin must ship atomically with the translation change or those two
   services halt with `FROZEN_RULE_CHANGED` on their next integrity check.
3. **Not authorized.** The approval is expired, the arm token fails, the
   absolute deadline has passed. `L5_CONTROLLER_OPEN` is unset.
4. **`maker_class` is always `None` on this path**, so every autonomous
   opening lands on `T_UNEVALUABLE` and is answered by `MAKER_TREATMENT` —
   the same treatment production already applies to L5 actions, which are
   also handed `{}`. No queue-position evidence exists for an order that
   has not been placed, and inventing a class would be fabricating
   evidence.
5. **Unproven against a live venue.** Every assertion is against
   `FakeTransport`. No autonomous opening has reached PMUS.
