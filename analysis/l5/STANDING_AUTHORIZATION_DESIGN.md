# Standing bounded mandate — design and migration plan

**Finding up front: ~85% of this already exists and runs.** The recurring
manual Railway ceremony is not caused by a missing architecture. It is caused
by **three code constants and one storage choice**. Everything else — multi-window
claims, per-class action budgets, durable spent-window ledgers,
refuse-on-widening, independent halt, auto window discovery, continuous shadow
— is built, deployed, and counting today.

No new service. No new volume. No new dependency. No new cost.

---

## 1. What exists and is reused unchanged

| requirement | existing component | evidence it already works |
|---|---|---|
| multi-window from one approval | `l5_campaign_approval.authorize_window` + `WindowLedger` | *"the WINDOW claim is keyed on mandate-plus-market, decided by the same O_EXCL call"*; resuming the same slug after restart does not spend a second window |
| verify without spending a window | `reserve=False` / `would_reserve` | added after verification consumed the 1600z window on 2026-10-20 |
| **executor cannot widen its own authorization** | `SCOPED_LIMIT_FIELDS` | *"Arming refuses if the running limits are LOOSER than what was approved, so widening a cap after approval invalidates the authorization"* — **requirement 5, already met** |
| composition of two limit sources | `authorize_arming` | *"THE INTERSECTION, NEVER THE WIDER OF THE TWO"*; unreadable on either side is a refusal |
| per-class action budgets | `l5_live_authorization` class budgets | live now: OPEN 3/16, COMPLETE 2/24, REPLACE 1/32, submissions 6/56 |
| absolute owner deadline | `NOT_AFTER_ENV` | belt-and-braces with the claim TTL |
| series restriction | `SERIES_ENV` + `ALLOWED_SERIES` | code allowlist, document may only narrow |
| single-use claims | `claim_once` O_EXCL | ledger write precedes any order; a crash costs a window, never grants one |
| **independent halt the executor cannot clear** | `l5_halt_gate` + `l5-halt-watchdog` | watchdog owns the volume, executor's only verb is GET; *"An executor that cannot find out whether it is halted is halted"* |
| maker-only enforcement | `EXECUTION_MODE` + `l5_runtime_config_verify` row | proven both ways this session: flag on → FAIL, off → PASS |
| exposure / loss / inventory limits | `l5_live_validation_gates` | $5 daily, $20 cumulative, residual cap 3, cash envelope |
| campaign worst-case budget | `l5_campaign_budget` | `headroom = budget − (realized + unsettled + outstanding)`; release requires venue evidence |
| auto window discovery + retirement | runner + `complement_retire_from_start` | rolled 1445z → 1515z unattended in today's logs |
| reconcile / fail closed on stale | `reconcile_from_venue`, `reconcile_stranded`, `BOOK_STALE`, `GAP_SUSPECTED` | every unreadable input is a refusal, never a zero |
| **continuous shadow independent of live** | `l5_two_sided_shadow` | ran all session under `SHADOW_ONLY_LOCK`; `simulate=True` bypasses the live flags by design — **requirement 8's first half, already met** |
| signing primitives | `cryptography>=42.0`, Ed25519 in `pmus_auth` | already a dependency; no new package |

## 2. The four things actually blocking a standing mandate

1. **`MAX_CAMPAIGN_S = 4 * 3600`** clamps any approval to four hours from
   issue. `state()` enforces it, so a document asking for a month gets 4h.
2. **`MAX_WINDOWS = 8`** is a *total per nonce*, not a rate. Eight windows and
   the mandate is spent.
3. **`WindowLedger` is keyed on `nonce_sid` alone**, so exhausting a mandate
   requires the owner to mint a new nonce — the repeated-nonce problem.
4. **`EnvApprovalStore` reads a Railway variable.** Anyone who can write
   Railway can write the authorization. My own token wrote three variables
   this session, which is precisely why requirement 3 is not met today: the
   design comment claims *"Claude cannot write it, so Claude cannot approve a
   campaign"* and that is **false in practice**.

## 3. The design — authenticity, not custody

The fix for (4) is **not** to move the document somewhere I cannot reach. It
is to make writing the bytes insufficient.

**The mandate becomes a signed document.** The owner signs it once, offline,
with an Ed25519 private key they alone hold. The executor verifies against a
public key compiled into the image. I can still set the variable; I cannot
produce a document that verifies. Custody of the medium stops mattering, which
also means this needs no new store and no new service.

```
owner's offline key ──signs──> mandate (JSON + sig)
                                   │
                        set once, any medium
                                   ▼
                     executor verifies with PUBLIC key
                                   │
             ┌─────────────────────┼─────────────────────┐
             ▼                     ▼                     ▼
      rate ledger            scoped limits          promotion gate
    (per UTC day)         (refuse on widening)   (shadow expectancy)
             └─────────────────────┼─────────────────────┘
                                   ▼
                      halt latch (watchdog, GET only)
                                   ▼
                            maker-only submit
```

**Standing validity with rate limits, not totals.** The mandate declares
`not_before` / `not_after` up to a new `MAX_MANDATE_S` (30 days), and instead
of a total window count it declares **per-UTC-day rates**:
`max_windows_per_day`, `max_submissions_per_day`. The spent-window ledger key
gains a day component — `camp1.<nonce>.<YYYY-MM-DD>.json` — so the counters
roll at midnight without the owner touching anything. No new timestamps, no
new nonces, and the existing O_EXCL claim logic is untouched.

**The promotion gate (requirement 8, second half).** A standing mandate is
only safe to leave in place if it will not trade a losing strategy. So arming
additionally requires the rolling shadow expectancy to clear a threshold the
*mandate* states:

```
permit_live  iff  settled_windows >= min_sample
             and  net_per_settled_window >= mandate.min_expectancy_usd
```

Both inputs already exist in the heartbeat —
`economics.per_scenario.CONSERVATIVE.net_per_settled_window` and
`windows_settled`. This is the single most valuable new piece, and it has an
immediate consequence the owner should see clearly:

> **On today's measurement (−$0.2065 per settled window over 282 windows), the
> promotion gate would refuse to arm.** The standing mandate could be installed
> and left in place, and the system would stay in shadow until the measured
> expectancy turns positive, then begin trading on its own — bounded, and
> without another Railway change.

That is the answer to "eliminate the manual ceremony" that does not also mean
"trade a negative edge continuously".

**Revocation (requirement 7), three independent paths, all existing media:**

1. **Halt latch** — assert on the watchdog. No redeploy, survives restarts,
   executor cannot clear. Fastest.
2. **Revocation list** — the watchdog serves `revoked.json` next to the latch
   on the volume it already owns; the executor GETs it and refuses any mandate
   whose nonce appears. Unreachable or unparseable = refuse. Reuses
   `HttpHaltStore`'s exact shape and fail-closed contract.
3. **Replace the document** — clear or overwrite the variable. Requires a
   redeploy, so it is the slowest and listed last.

Automatic shutdown on risk/operational failure is unchanged: daily halt,
cumulative gate, campaign budget, watchdog, reconcile-uncertainty refusals.

## 4. Requirement-by-requirement

| # | requirement | met by | new code? |
|---|---|---|---|
| 1 | reuse executor/watchdog/reconcile/risk, no new service | all of §1 | none |
| 2 | durable standing policy, no manual timestamps or nonces | `MAX_MANDATE_S` + per-day rate ledger | small |
| 3 | separate dev permissions from runtime authorization | Ed25519 signature; public key in image, private key offline | small |
| 4 | maker-only, exposure/loss limits, independent halt | `EXECUTION_MODE`, validation gates, halt gate | none |
| 5 | never widen / reset losses / clear halt / renew expiry | `SCOPED_LIMIT_FIELDS`; no private key; halt is assert-only; expiry is in signed bytes | none |
| 6 | auto discover/retire windows, reconcile, fail closed | runner + retirement + reconcile | none |
| 7 | immediate revocation, automatic shutdown | halt latch, revocation list, existing gates | small |
| 8 | shadow independent; no auto-promotion of a loser | shadow already independent; **promotion gate** | new, small |
| 9 | one-time owner approval for future bounded mandates | sign once, valid up to 30 days, rates roll daily | — |
| 10 | smallest changes + migration | §5 | — |

## 5. Smallest changes — five files, ~320 lines, no new module except one

| file | change | ~lines |
|---|---|---|
| `src/l5_campaign_approval.py` | `MANDATE_PUBKEYS` constant; `verify_signature()` (Ed25519, existing dep); accept `kind: "standing"` with `MAX_MANDATE_S = 30d` while `kind: "campaign"` keeps `MAX_CAMPAIGN_S`; `max_windows_per_day` / `max_submissions_per_day`; day component in `WindowLedger._path` | ~140 |
| `src/l5_live_authorization.py` | extend `SCOPED_LIMIT_FIELDS` with the two rate fields; new `L5_REQUIRE_MANDATE` so an **absent** document refuses instead of falling back to the single-window path | ~40 |
| `src/l5_promotion_gate.py` *(new)* | read rolling shadow expectancy + sample size, compare to mandate threshold, fail closed on missing/stale | ~110 |
| `src/l5_halt_gate.py` | `revocations()` beside the latch, same store, same fail-closed contract | ~35 |
| `src/l5_runtime_config_verify.py` | graded rows: `mandate_signature_required`, `mandate_required`, `promotion_gate_active`, `standing_rates_not_widened` | ~45 |

**Deliberately unchanged:** `l5_two_sided_probe` (sizing, 0.96 ceiling),
`l5_live_validation_gates` (all limits), `l5_campaign_budget`,
`l5_live_execution` (reconcile, exact-order cancellation), the runner's window
discovery, `l5_two_sided_shadow`, the watchdog service, and every risk number.

## 6. Migration plan — five phases, each verifiable under the shadow lock

**Phase 0 — key ceremony (owner only).** Owner generates an Ed25519 keypair
offline, keeps the private key, gives me the **public** key. I commit it as
`MANDATE_PUBKEYS`. I never see the private key; if I ever do, it is burned and
regenerated.

**Phase 1 — signature verification, enforcement off.** Land `verify_signature`
and the graded config rows. The verifier *reports* signature status; arming
still uses today's path. Shadow lock stays on. Proves verification works
against a real owner-signed document before anything depends on it.

**Phase 2 — rate ledger + standing kind, enforcement off.** Land the per-day
ledger key and `kind: "standing"`. Run with `reserve=False` so the executor
reports *"would this window be granted?"* across a full UTC day without
spending anything. Confirms rates roll at midnight and the counters are right.

**Phase 3 — promotion gate, reporting only.** Land `l5_promotion_gate` and
emit its verdict in the heartbeat every window. Expected result on current
data: **REFUSE**, expectancy −$0.2065 against any sane threshold. That
negative verdict is the acceptance test — the gate must refuse today.

**Phase 4 — enforcement on, still shadow-locked.** Flip `L5_REQUIRE_MANDATE`
and make the promotion gate binding. Verify the executor refuses to arm for
the right reason. Still no live order: `L5_SHADOW_ONLY=1`.

**Phase 5 — standing live, owner action, one time.** Owner installs the signed
standing mandate and clears the shadow lock once. From then on the system arms
itself window by window whenever every gate agrees, retires and reconciles
automatically, and halts on expiry, revocation, latch, budget, loss limit or
reconciliation uncertainty — with no further Railway changes.

**Rollback at every phase** is the halt latch plus `L5_SHADOW_ONLY=1`, exactly
as today.

## 7. Honest limits of this design

- **Phase 5 will not start trading on today's evidence.** The promotion gate
  refuses at −$0.2065/window. The architecture removes the ceremony; it does
  not create edge. If the intent is to trade continuously *now*, that requires
  either a positive shadow or an explicit owner override of the promotion gate
   — and an override is a standing licence to trade a measured loser, which I
  would implement only if you ask for it in those words.
- **The signature closes the authorization gap, not the deploy gap.** Anyone
  with Railway write access can still redeploy a different image or change
  risk variables. Closing that needs branch protection and a pinned image
  digest, which is a separate piece of work and not in this plan.
- **Per-day rates are a rate limit, not a budget.** The cumulative $20
  account-wide gate and the campaign worst-case budget remain the dollar
  bounds; the rates only bound activity.
- **I have not implemented any of this.** This is a design against inspected
  code, with line estimates, not measured diffs.
