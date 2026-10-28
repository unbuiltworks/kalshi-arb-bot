# Reprice before the gates — and what driving it end to end revealed

Owner decision: use reprice-before-gates; do not promote the post-gate
integrity exception. Done, and the post-gate exception is reverted in full.

## What changed

The fresh-book repricing now runs **upstream of every gate**, in the same
position and for the same reason the queue-measurement override was moved
upstream — its own comment: *"Built after the gates, this block mutated a
money-moving field behind them... Now it rewrites dec and tr upstream, so
maker_only, the imbalance suppressor, the validation gates and the residual
cap all evaluate the measurement itself."*

Measured source order inside `handle`: reprice at offset 46,501 →
`gated_money` captured at 64,163 → pre-send cross guard at 75,392 →
integrity check at 82,628.

* `gated_money` is captured **after** the reprice, so the gates judge the
  price actually sent.
* The byte-for-byte integrity check is **unconditional again**. The
  `REQUOTE_NOT_BELOW_GATED_PRICE` exception is gone, and so is its entry in
  the executor refusal inventory.
* The send boundary **fails closed and changes nothing**: if the book has
  moved far enough that the gated price would now cross, the order is not
  sent. An already-approved money intent is never edited.
* The helper is renamed `fresh_book_requote` / `L5_FRESH_BOOK_REQUOTE`,
  because "presend" had become actively wrong about where it runs. It was
  never deployed, so there is no migration.

## The integration tests found something the design did not predict

Driving a real `FaithfulExecutor` against a real store and a fake venue,
with repricing enabled, across all four inventory states a crossing frozen
plan can meet:

| inventory | what resolves the cross | reprice fires | price sent |
|---|---|---|---|
| flat | round **retired** and re-planned from the current book | no | 0.41 |
| imbalance | **complement** path, `DERIVED_FROM_CURRENT_BOOK` | no | 0.41 |
| same side held | neither applies | no | nothing sent |
| matched | the **other leg** is selected, and it does not cross | no | 0.35 |

In every state the price reaching the wire is derived from the current
book, the stale 0.6000 is never sent, and the store is never left
uncertain. **And in no state does the pre-gate reprice fire.** Three
existing mechanisms resolve a crossing plan before it can.

That is pinned as a test —
`test_the_pregate_reprice_has_no_reachable_state_in_this_path` — which is
written to start failing when a reachable state is found, at which point
the reprice can be judged on its merits.

## The remaining economic blocker, sharpened

The requote was authorized to fix a throughput bottleneck diagnosed from 23
consecutive `PRESEND_WOULD_CROSS:UP` refusals over 267 s of the live 0015z
window. The integration harness cannot reproduce that state, and the three
mechanisms above say why it should not have arisen. So one of these is
true, and which one decides whether the feature is worth anything:

1. the live state involved something the harness does not model — the most
   likely candidate is a **resting order** blocking retirement, since the
   0015z tape also carries `SAFETY_OVERLAY:MAX_LIVE_ORDERS` rows; or
2. the engine deployed at the time differed from the code under test; or
3. the bottleneck diagnosis attributed to a stale price what was really a
   different refusal.

**Until that is settled, enabling `L5_FRESH_BOOK_REQUOTE` is not justified
by anything measured.** This is the same shape as the earlier
complement-adoption finding: a mechanism believed missing turned out to
already exist. Identifying the exact live state from the preserved 0015z
tape is the prerequisite, and it is cheap — the tape is committed at
`analysis/l5/requote_replay/decisions_20261028.jsonl`.

## Runtime config verifier

`APPROVED` was campaign 1's profile frozen in code, and eight rows
disagreed with the configuration the owner had since approved, so
`L5_EFFECTIVE_RUNTIME_CONFIG` read `PASS: false` on a correctly configured
service — about to be used as the pre-arm check for a live campaign.

Now:

* figures the **document** carries (`max_total_exposure_usd`,
  `max_windows`, `max_daily_loss_usd`) are read from the campaign in force
  and **intersected with the code ceiling**, so an approval can never widen
  one;
* figures that are **code ceilings** stay graded against the code, and a
  document cannot move them;
* the per-campaign submission budgets, which the document has no field for,
  are graded as **invariants** — configured, positive, within the ticket
  ceiling — rather than against a number that must be hand-edited.

It is emphatically not an unconditional PASS. With no document, an
unparseable one, an unapproved one or an expired one, the document-derived
rows report `NO_APPROVAL_IN_FORCE` and **fail**; an unset or zero budget
fails; a deployed exposure above the approved one fails; a stale tighter
window budget fails; a raising accessor fails its row. Verified on the real
deployed configuration: `PASS = True`, 26 rows, none failing.

## Deployment readiness

| check | state |
|---|---|
| shadow lock | **engaged** — `locked: true`, `blocked: ["submit"]`, cancel still permitted |
| arming | refused, `SHADOW_ONLY_LOCK` |
| absolute deadline | EXPIRED |
| campaign approval | `approval_stale`, `reserved_nothing: true`, document preserved |
| outstanding orders | **0** (`live_orders: 0`) |
| durable accounting | survived the restart (`largest_recorded_clip: 37.73`) |
| halt gate | PASS, permitted |
| requote | **disabled** — `L5_FRESH_BOOK_REQUOTE` unset |

**One readiness item I cannot confirm from telemetry, and will not claim.**
Flat *live* inventory and venue reconciliation are not observable while
shadow-locked: the preflight reports `order_state_and_reconciliation` as
`NOT_EVALUATED — needs an authoritative venue read`, and that read happens
inside the arming path. The residual figures visible in the heartbeat are
**shadow-modelled** cells (`windows_observed: 806`, `modelled_net`), not
live positions, and must not be read as inventory. The last authoritative
statement is the campaign's own settlement reconciliation: four windows
settled, +$0.7083, residual flat.

So the promotion procedure must read the venue immediately before
promoting and abort if anything is live or unresolved. Deploying while
flat is a precondition and not a preference: a changed claim namespace
makes cancel markers and accumulation claims written under the old
unsuffixed id invisible to the new one.
