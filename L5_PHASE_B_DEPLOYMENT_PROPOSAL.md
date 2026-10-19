# Phase B deployment proposal + first settled-window economics

Date: 2026-10-19. Branch `claude/gemini-btc-economic-viability-h3u7zl`.
Production unchanged throughout: deployment `f3b65e78` / commit `23c45806`.

---

## 0. Correction, then the recommendation

**I reported "zero new regressions" on the Phase B tree, and that was
wrong.** The claim is in `2ceabfb3`'s commit message. The full suite on the
corrected tree surfaced four failures absent from the baseline file I was
diffing against, and two of them are the invariant that matters most:

```
FAILED tests/test_l5_one_submission_claim.py::
       test_every_reachable_submission_path_goes_through_the_claim
FAILED tests/test_l5_mechanism_strategy_isolation.py::
       test_the_mechanism_is_the_only_submit_call_site_reachable_in_this_mode
```

`l5_intent_gate.py` had added a **third `transport.submit` call site**, in
`submit_controller_intent`, which did not spend a single-use
`claim_submission`. What protects this account is the rule that no path
reaches the wire without spending that claim in the same module as the
send. My adapter was a second unclaimed door — the precise thing the
invariant exists to prevent — and it contradicted my own review, which had
said that routing a generated decision to the venue is a money-path change
and is not authorized. **A test caught it; my review did not.**

Verified by running the two tests at the deployed commit `23c45806` in a
clean worktree: they **pass** there and **fail** on the Phase B tree, so
this was Phase B's regression and not pre-existing.

(The other two — `test_chain_ca_fallback` and
`test_gemini_write_path_isolation` — **also fail at `23c45806`**. They are
pre-existing, unrelated to any of this work, and my baseline file simply
did not contain them. The baseline has been regenerated from a clean
worktree at the deployed commit rather than from a stored list.)

**The fix: the wire is removed, not registered.** `SUBMIT_SITES` stays at
its two claim-gated routes. `l5_intent_gate` now owns no send at all, and
what is left is everything that is *not* the send, as separately testable
steps for the claim-gated route that will one day call them:

- `prepare_controller_submission` — chain, authorization, store-API
  precondition, duplicate guard, wire payload. Writes nothing, sends
  nothing.
- `record_controller_submission` — the durable record that must exist
  *before* a wire.
- `resolve_controller_submission` — classifies an outcome *someone else's*
  send produced (`{"ack": …}` or `{"error": …}`), attaches the venue id,
  proves ownership, marks the book uncertain. Never touches a transport.
- `submit_controller_intent` — runs every precondition and then refuses:
  `CONTROLLER_SUBMISSION_HAS_NO_CLAIM_GATED_ROUTE`, naming where a send
  would have to live.

This is stronger than the absent authorization I was relying on before:
production cannot submit a controller intent because **there is no route**,
not because a permission is withheld. Three new tests pin it, including one
asserting no `transport.submit` appears anywhere in the module.

## 0b. Recommendation

**Promote Phase B in shadow-only mode: YES, on the corrected tree, with one
condition.** The condition is that the same push carries the
settlement-read fix in section 2, because without it the deployed system
cannot produce the numbers in section 1 by itself — it has been polling a
route that discards the answer.

What makes it safe is not that it is well tested — I have just demonstrated
the limits of that — but that it **adds no capability and no route**. The
controller path ends in a refusal with no send behind it, and the two
claim-gated routes are unchanged.

What is **not** proposed: no live orders, no limit change, no capital
change, no new service, no new branch, no new executor, and no third entry
in `SUBMIT_SITES`.

## 1. Item 7 — conservative / base / optimistic net economics from settled
BTC15m windows

Three BTC15m windows have closed under the queue-aware model and have an
authoritative venue settlement price:

| window | venue state | `settlementPx` (UP) |
|---|---|---|
| 1700z | MARKET_STATE_EXPIRED | 0.0000 |
| 1715z | MARKET_STATE_EXPIRED | 1.0000 |
| 1730z | MARKET_STATE_EXPIRED | 0.0000 |

Every number below is **MODELLED**. No order was submitted, no fill was
confirmed, nothing is realized. Maker fee is the CONFIRMED $0.0000 measured
over 293 actual fills on this account; the maker rebate is reported
UNCREDITED and accrued at zero.

### Net, per policy, per scenario, over the three settled windows

| policy | CONSERVATIVE | BASE | OPTIMISTIC | mean/window (cons.) |
|---|---|---|---|---|
| ARGMAX (baseline) | **+$0.87** | +$0.87 | +$0.87 | +$0.29 |
| RESIDUAL_URGENCY | **+$0.57** | +$0.57 | +$0.57 | +$0.19 |
| PAIR_AVAILABLE | **−$0.55** | −$0.55 | −$0.55 | −$0.18 |

Per window, conservative scenario, with all unfilled and stranded
positions included (a position left on the books at expiry is priced at the
venue's settlement price, never marked to an expired book; a resting order
at expiry is dropped, never credited):

| policy | window | matched | residual | cost | redemption | net |
|---|---|---|---|---|---|---|
| ARGMAX | 1700z | 3 | +3 | $2.95 | $3.0000 | +$0.05 |
| ARGMAX | 1715z | 0 | +1 | $0.47 | $1.0000 | +$0.53 |
| ARGMAX | 1730z | 2 | 0 | $1.71 | $2.0000 | +$0.29 |
| RESIDUAL_URGENCY | 1700z | 2 | +2 | $2.17 | $2.0000 | −$0.17 |
| RESIDUAL_URGENCY | 1715z | 0 | +1 | $0.47 | $1.0000 | +$0.53 |
| RESIDUAL_URGENCY | 1730z | 3 | 0 | $2.79 | $3.0000 | +$0.21 |
| PAIR_AVAILABLE | 1700z | 1 | +1 | $1.18 | $1.0000 | −$0.18 |
| PAIR_AVAILABLE | 1715z | 1 | −1 | $1.06 | $1.0000 | −$0.06 |
| PAIR_AVAILABLE | 1730z | 1 | +1 | $1.31 | $1.0000 | −$0.31 |

There is no stranded inventory left over from these three windows: all
inventory is accounted for above. 1745z was still open when this was
written and is excluded.

### Separated economics, as `CLAUDE.md` requires

Average-cost basis (the controller's own convention, and the basis
`complement_target` prices against):

| policy | matched-pair net | directional residual net | total |
|---|---|---|---|
| ARGMAX | +$1.2550 | −$0.3850 | +$0.87 |
| RESIDUAL_URGENCY | +$0.7400 | −$0.1700 | +$0.57 |
| PAIR_AVAILABLE | +$0.4550 | −$1.0050 | −$0.55 |

FIFO matching, as a sensitivity (the total is identical either way; only
the split moves, so the split is convention-dependent and the total is
not):

| policy | matched-pair net | residual net |
|---|---|---|
| ARGMAX | +$0.88 | −$0.01 |
| RESIDUAL_URGENCY | +$0.48 | +$0.09 |
| PAIR_AVAILABLE | +$0.12 | −$0.67 |

Spread capture is inside the pair line (every quote was a maker join);
fees are zero and measured; rebates are zero and uncredited; there are no
liquidity rewards on this account; settlement cashflow is the redemption
column. Nothing is summed across those categories.

### Three findings that matter more than the nets

**(a) The three scenarios have not diverged at all.** CONSERVATIVE, BASE
and OPTIMISTIC produced byte-identical inventory in all nine simulations.
That is not a wiring fault: all 36 settled-window fills had a **known**
queue (36/36), so the OPTIMISTIC-only unknown-queue sweep path never
activated, and every fill's trade volume cleared `queue_ahead + qty`, which
is the strictest (CONSERVATIVE) bar. The margin is thin, though — median
volume through the level was only 1.23× the queue ahead of us, minimum
1.01× — so divergence should appear as soon as a fill lands on a thinner
level. **Until it does, "conservative" here carries no extra safety over
"optimistic": they are the same measurement.** That is the honest reading.

**(b) Adverse selection is the dominant execution fact.** 9/14, 9/13 and
6/9 fills (64%, 69%, 67%) were adverse — the wire moved away from our
outcome after we quoted. We are being filled as the market leaves. This is
what a paid liquidity provider signs up for, but it means the pair line is
the only thing earning, and it has to earn enough to pay for this.

**(c) ARGMAX is ahead of RESIDUAL_URGENCY, and the reason is instructive.**
Per the standing instruction, RESIDUAL_URGENCY is **not promoted**: it does
not show better conservative net economics. On 1730z it completed one
*more* pair than ARGMAX and still earned less ($0.21 vs $0.29), because at
tte≈95s it EXTENDed YES at $0.64 on a surplus whose basis was $0.28 —
doubling its own basis 1.5 minutes from expiry. The two traces, verbatim:

```
ARGMAX  1730z: open YES@0.28 | extend YES@0.28 |
               complete NO@0.68  avg_surplus 0.2800  pair 0.9600 |
               complete NO@0.47  avg_surplus 0.2800  pair 0.7500
RESID   1730z: open YES@0.28 | extend YES@0.28 |
               complete NO@0.68  avg_surplus 0.2800  pair 0.9600 |
               extend YES@0.64 |
               complete NO@0.47  avg_surplus 0.4000  pair 0.8700 |
               complete NO@0.44  avg_surplus 0.4000  pair 0.8400
```

ARGMAX's two pair costs sum to exactly its window cost ($1.71) because it
bought nothing after completing. RESIDUAL_URGENCY's do not ($2.67 of
completes against $2.79 of window cost): the $0.64 extend is the
difference, and it bought a unit that then had to be completed at a worse
basis. More pairs, more units at risk, less money. The late-EXTEND defect
we diagnosed in the argmax collapse is **not fixed by RESIDUAL_URGENCY**;
it survives in it.

**The 0.96 pair cap held everywhere, and that was checked rather than
assumed.** Every COMPLETE across all nine settled-window traces came in at
or under the cap on the controller's own average-cost basis — the maximum
is exactly 0.9600, hit five times, which is `complement_target` pricing to
the cap and not past it. A FIFO re-pairing of the same fills shows one
1.08 "pair" in RESIDUAL_URGENCY 1730z; that is an artifact of pairing
oldest-to-oldest, not a breach, as the trace above shows.

### And the caveat that governs all of the above

**n = 3 windows, ~45 minutes, 36 modelled fills, $5.13 / $5.43 / $3.55 of
modelled cost.** Two windows settled UP=0 and one UP=1. ARGMAX's +$0.87 is
dominated by a single residual YES that happened to settle at 1.0000 on
1715z (+$0.53 of the +$0.87 is that one unit's coin flip). Pair net is
positive for all three policies and residual net is negative for all
three — that ordering is the only thing in this data with a mechanism
behind it. **This is not evidence of edge, and it is not evidence against
the $480/day target either; it is the first three readings of an
instrument that only started working today.** Do not size anything off it.

---

## 2. A defect this exposed, and the fix in this proposal

`shadow_settle` read the venue through `OBS.pmus_bbo`. That is a **book**
reader: an expired market answers `/v1/markets/{slug}/bbo` with
`settlementPx` present and `bestBid`/`bestAsk` both **null**, which
`pmus_bbo` treats as an empty answer, falling back to `/book` — a route
that carries no `settlementPx` at all. So the settlement price was
discarded for precisely the markets that have one. In production:
`shadow_awaiting_settlement = 201`, `windows_settled = 0`, three closed
windows reported stranded, against a venue that had already published all
three numbers.

Every existing test monkeypatched the read with a dict containing
`settlementPx`, so not one of them could catch it. Same class of defect as
the Decimal-vs-string fixture bug: **the fixture was kinder than the
venue.**

The fix:

- `src/l5_pmus_realtime_observer.py`: new `pmus_settlement(slug)` — reads
  `/bbo`, returns `state` and `settlementPx` verbatim, **no book
  fallback**, `None` on a read failure (a failed read is not an unsettled
  market). `pmus_bbo` is **not touched**.
- `src/l5_pmus_live_runner.py`: `shadow_settle` calls `pmus_settlement`.
  One line, plus the docstring recording why.
- Tests: the five existing monkeypatches repointed, plus five new tests
  built on the venue's **actual** document shape — that the settlement read
  returns the price for a null BBO, that `pmus_bbo` discards it (the live
  defect, pinned, so merging the two reads fails the suite), that
  `shadow_settle` never consults `/book`, that a failed read strands rather
  than settles, and that `pmus_bbo`'s own behaviour on a live market is
  unchanged on both its paths.

Blast radius: `pmus_bbo` has exactly one remaining caller in `src/`, the
observer's own copyability recorder, which is a book read and wants a book
read. **The money path does not call `pmus_bbo` at all**, so this change is
shadow-only in effect as well as in intent.

Verification that it works against the real venue, not a fixture: the three
settled-window inventories were replayed from the production fill records
through the **shipped** `ShadowController.settle`, with the settlement price
read live through the new `pmus_settlement`. It reproduced section 1 exactly
— `windows_settled: 3`, `stranded_markets: 0`, headline nets $0.87 / $0.57
/ −$0.55.

---

## 3. Phase B review against the complete executor chain

`Runner.handle`'s prefix was the easy part. The real chain is
`l5_live_execution.handle`, **lines 4048–5026, 979 lines, 30 `prov.refuse`
sites**. `IntentGate.evaluate` reproduces 12 layers. Three divergences were
found:

**(a) `evaluate()` is not the complete chain, and said it was.** Returning
`admissible: True` from a 12-layer subset of a 42-layer chain is the kind
of claim that reads as ready. Fixed: `admissible` → `cleared_here`,
`complete_chain: False` and `not_evaluated_here` on every result, and
`EXECUTOR_GATES_NOT_EVALUATED_HERE` enumerating all 30 executor refusal
sites by layer — with a guard test that counts `prov.refuse` in the
executor's source and fails if the inventory stops matching it.

**(b) Gate-order difference, declared rather than papered over.** The
executor reads the durable halt latch **first**, before the shadow lock;
`GATE_ORDER` reads `SHADOW_ONLY_LOCK` then `DURABLE_HALT_LATCH`. Recorded
in `GATE_ORDER_PROVENANCE`, with a test proving a both-tripped state still
refuses under either order, so the difference cannot admit anything the
executor would refuse. It changes which *reason string* appears, not
whether anything gets through.

**(c) `CURRENT_WINDOW` added a check `Runner.handle` never had** — the
intent's market against the window's slug. Proved a no-op by asserting
`OBS.pmus_slug_for(ws) == AB.slug_for(ws)` across the tested windows, and
pinned with a test so it stays a no-op.

**No bypass was found**, and that is asserted rather than assumed: a
parametrised matrix drives operator halt, shadow lock, durable latch, owner
arm token, transport armed, account-wide exposure, registry ownership and
reconciliation certainty against **both** an L5 intent and a controller
intent and requires the same blocking gate and the same reason from each.
With every gate green, the controller intent is still refused at the
boundary and the executor is never called; the positive control proves an
L5 intent with everything green does reach it, so the test cannot pass
against a gate that merely refuses everything. `vars(gate)` is exactly
`["runner"]` — the gate holds no copy of any limit or latch.

L5's own path is **not refactored and not wrapped**: it still reaches
`ex.handle(row, {}, market=, window=, open_markets=)` through the same call
with the same arguments.

---

## 4. The adapter, and why it cannot submit in production

`ControllerLiveAuthorization(granted_by, market, max_size, note)` is a
**constructor argument** to `IntentGate`. Production constructs
`IntentGate(runner)` — one argument — so the authorization is **absent**.
Two tests assert that no module under `src/` ever constructs one and that
it is never read from the environment, so this is not a flag that a Railway
variable could flip.

The order of operations is unchanged in substance from the draft — full
chain → `REQUIRED_STORE_API` precondition against the real order store
(`already_submitted`, `record_submit`, `attach_venue_id`, `mark_uncertain`,
`owns_venue_order_id`, `terminate_order`, `save`) → duplicate guard on the
deterministic `ctl-<policy>-<scenario>-<ref>` client order id →
`record_submit` **before** any wire → (send) → ambiguity handling →
ownership check after the ack — except that the send is **not here**, per
section 0. `REFUSED_BEFORE_ORDER_POST` does not halt;
`SUBMIT_OUTCOME_UNKNOWN`, `ACK_WITHOUT_VENUE_ID`, `ACK_NOT_PERSISTED` and
`OWNERSHIP_NOT_SERVABLE_AFTER_ACK` each `mark_uncertain` and demand a halt.
Those four are now exercised through `resolve_controller_submission`, with
the test performing the send its fake transport, so the semantics stay
tested while the module stays unable to reach a venue.

An earlier draft of this adapter called `reserve`/`release`/
`record_submitted`/`owns_client_order_id` — methods that do not exist.
Unreachable in production, so it would never have raised, which is exactly
why code like that is dangerous: it reads as ready. The
`REQUIRED_STORE_API` precondition is there so the next such mistake refuses
instead of looking fine.

Fault injection, 85 tests: crash recovery (the pre-send record survives and
the retry is a duplicate), ambiguous submission, stale inventory,
duplicate intents, HALT transitions mid-submission, account-wide exposure
aggregation through the runner's own `account_exposure_lines`, a
submission-gate read that raises, an exposure read that raises, a runner
with no exposure reader, a store with no ownership oracle, no registry, a
missing executor or transport, and a parked order the venue has not
confirmed.

---

## 5. Deployed state, verified unchanged

Production heartbeat 2026-10-19T17:46:22Z, deployment `f3b65e78`, commit
`23c45806`:

- `submission_gate`: `blocked: true`, `reason: SHADOW_ONLY_LOCK`,
  `halt_latch: CLEAR`, `owner_arm_token: false`, `armed: false`
- `order_progress`: `live_orders: 0`, `parked_orders: 0`
- `account_exposure`: `account_total_worst_case_usd: "0"`, `ok: true`
- `limits`: unchanged — $5/market, $5 total, 1 concurrent market, 1 live
  order, $5 daily realized loss halt
- `quarantined`: `[]`, `preflight: PASSED`
- nine simulations live: 666 decisions per policy, real ladder queue
  positions, no `controller_error` of any kind
- observer fan-out intact: `primary: true`, `extra_observers: 1`,
  `extra_errors: {}` — the money path's observer is called first and
  unwrapped

Commit `abd3dcf3` (Phase B) was pushed to the branch and Railway recorded
it **SKIPPED** (`17b62ff1`), because the watch path is
`deploy/l5_pmus_live/DEPLOY_TRIGGER` only and the trigger was not touched.
That is the instruction honoured and independently confirmed by the
platform.

---

## 6. What a deploy would actually ship

Commits on top of `23c45806`:

| commit | content | path |
|---|---|---|
| `abd3dcf3` | `l5_intent_gate.py`; `Runner.handle`'s three refusals delegated | **money path** |
| `2ceabfb3` | complete-chain declaration, gate-order provenance, the adapter (with the defective wire call — see section 0) | **money path** |
| (this change) | the wire call removed; adapter split into prepare / record / resolve | **money path** |
| (this change) | `pmus_settlement` + `shadow_settle` repointed | shadow only |

**The honest warning: these are no longer separable in this tree.** The
runner imports `l5_intent_gate` and `Runner.handle` uses it, so the next
`DEPLOY_TRIGGER` push carries the money-path change whether or not the
settlement fix is what you want. It adds no capability and no variable, but
it is a money-path promotion and should be approved as one. Splitting them
would mean reverting Phase B on this branch and re-landing it, which is
more churn and more risk than reviewing the diff.

Suite on the tree, against a baseline regenerated from a clean worktree at
`23c45806` rather than from a stored list — that stored list is what let
section 0's regression through, so it is not used again. The pre-existing
failing set is the `test_spread_scanner` /
`test_window_forensics_stream` / `test_pmus_status_live_handoff` group
plus `test_chain_ca_fallback` and `test_gemini_write_path_isolation`, all
unrelated to this work. The comparison run is attached to this change.

### Verification plan for the first 30 minutes after the push

1. Deployment reaches SUCCESS; `L5_SHADOW_READY` for all three policies.
2. `submission_gate.reason == SHADOW_ONLY_LOCK`, `armed: false`,
   `live_orders: 0`, exposure `"0"` — i.e. section 5 again.
3. `L5_SHADOW_SETTLED` appears **immediately**, not at the next window
   close: 1700z, 1715z and 1730z are still sitting unsettled in the durable
   state on the volume with their inventory intact, so the first
   `shadow_settle` tick after the deploy should settle all three at the
   prices in section 1 and reproduce those nets from the deployed process
   rather than from this analysis. If it does not, the fix is wrong.
   `shadow_awaiting_settlement` should stop being the only counter moving.
4. `economics().per_scenario[*].windows_settled` > 0 in the heartbeat.
5. No `controller_error:*` and no `shadow_settle_read_error`.
6. `frame_fanout.extra_errors` still `{}`.

If (2) is not exactly as stated, roll back to `f3b65e78` — it is still
marked `canRollback: true`.

---

## 7. Smallest remaining blockers to protected live validation

In order:

1. **Scenario divergence has not been demonstrated.** Conservative and
   optimistic are currently the same number. Until a fill lands on a level
   where they disagree, the conservative column is not yet doing the job it
   exists to do, and promotion decisions resting on it rest on an
   untested discriminator.
2. **The late-EXTEND defect is unfixed.** It is in ARGMAX and it survives
   in RESIDUAL_URGENCY. A policy that will not double its own basis inside
   the last two minutes is the smallest evidence-supported correction
   available, and the 1730z trace is the evidence for it.
3. **There is no claim-gated route for CONTROLLER_GENERATED provenance**,
   and after section 0 that is the stated blocker rather than a gap to
   paper over. A controller intent has never reached the real executor,
   not even to be refused by it: the 30 executor gates are declared, not
   exercised, for this provenance. Two steps, in order. First, a
   shadow-mode dry run that drives a controller intent into
   `l5_live_execution.handle` with the shadow lock engaged, so it is
   refused *by the executor* rather than before it — that needs no
   authorization, because the lock refuses it. Only then a send that
   spends `claim_submission` in its own module, which is a deliberate
   third entry in `SUBMIT_SITES` and wants approving as one.
4. **Sample size.** Three windows is not a basis for anything. Thirty to
   fifty settled windows, with the settlement read working, is the
   cheapest evidence left to buy and it costs nothing but time.
5. **Then, and only then**, a bounded live probe under the existing
   discipline: one market, one outstanding order, maker-first, no rebate
   assumption, settlement-only realized labelling.

Nothing in sections 1–6 is a reason to move capital.
