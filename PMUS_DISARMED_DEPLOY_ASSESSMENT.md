# GO/NO-GO: disarmed safety deployment

Nothing deployed. Nothing armed. No restart policy modified. Read-only
inspection plus local testing only.

**Verdict: GO for a disarmed safety deployment of `3dc9f135c`, NO-GO for
arming.** Two safety-critical findings below gate arming, neither of which a
disarmed deployment can trigger.

**Labels.** `[VENUE]` venue/platform-confirmed · `[INFERRED]` from terminal
book, not venue-confirmed · `[CODE]` implemented and tested · `[BLOCKED]`.

---

## 1. The 50 baseline failures, classified by live-trading safety relevance

I did not fix any of them. Classification only.

### A. SAFETY-CRITICAL — 9 failures, and they are dark, not merely red

`test_pmus_q1_executor.py` — **9 of its 12 failures share the exact root
cause I fixed in the canary.** Proven, not assumed: attaching a feed
in-process takes that file from **12 failures to 3**.

`pmus_q1_executor.run_campaign` never attaches the authenticated pricing
boundary either, so an armed run halts on `WS_PRICING_REQUIRED` before
reaching any of its own safety logic. The consequence is worse than a red
test: **these assertions never execute.** They cover
`test_fresh_ask_check_refuses_would_cross_before_post`,
`test_preflight_failures_place_no_order`,
`test_no_fill_cancels_exact_and_verifies`,
`test_one_sided_hold_respects_residual_cap_and_action`,
`test_ambiguous_fill_price_is_a_halt`,
`test_preview_rejection_halts_without_create`. Those are precisely the
protections a live probe relies on, and they are currently unverified.

**Direction of failure is safe** — it halts rather than trading — but this
blocks arming. `pmus_q1_executor` is the module `l5-pmus-live` watches and
the `pmus-q1-executor` service runs.

The remaining **3** in that file are a different cause and untriaged.

### B. SAFETY-RELEVANT but not on the BTC15m live path — 12

| file | n | why it matters, and why not here |
|---|---|---|
| `test_l5_live_probe_image_closure.py` | 2 | image-closure invariants — deployment hygiene, same family as the one my patch fixed |
| `test_case1_entrypoint.py` | 4 | arming/credential refusal for the Case-1 entrypoint, a different probe |
| `test_case1_authorization.py` | 1 | authorization pinning for Case-1 |
| `test_preflight_gate_isolation.py` | 1 | asserts the REST book is unreachable in the packaged artifact |
| `test_deploy_fallthrough_guard.py` | 1 | every deploy dir needs an explicit manifest |
| `test_r1_source_lineage.py` | 2 | R1 image lineage |
| `test_lane_a_probe_v0.py` | 2 | lane-A lineage checks |

These guard arming and packaging for **other** entrypoints. A disarmed
deployment of the oneshot cannot exercise them; arming anything in those
lanes should not proceed until they are triaged.

### C. NOT live-trading relevant — 29

`test_spread_scanner.py` (9) and `test_orderbook.py` (5) are Kalshi-era
market-data utilities, and the scanner file emits
`PytestUnknownMarkWarning: Unknown pytest.mark.asyncio` — a missing plugin,
i.e. environmental. `test_gemini_*` (6) are the Gemini/Kalshi lanes, a
different venue. `test_chain_ca_fallback.py`, `test_l5_chain_validation.py`,
`test_window_forensics_stream.py`, `test_ws_durability_entry.py`,
`test_continuous_variants.py`, `test_gemini_write_path_isolation.py` (6) are
chain/forensics/recorder-durability concerns off the execution path.

**Not fixing these.** A green total is not the goal and several are
environmental.

## 2. Branch HEAD vs active deployments `[VENUE]`

| | |
|---|---|
| research branch, local == remote | `f1aaa927d` |
| owning branch, local == remote | `3dc9f135c` (patch `b1f805df9` + chain test) |
| concurrent-session divergence | **none** — `git rev-list b1f805df9..origin/…` = 0 |

| service | branch / pin | restart | runs my patch? |
|---|---|---|---|
| `l5-pmus-live` `87f1834e` | `claude/gemini-btc-economic-viability-h3u7zl`, unpinned | **ALWAYS** | no |
| `l5-live-probe-oneshot` `84aa9999` | `claude/august-l5-forensic-replay-788cml` @ **`b88ec5eec`** | **NEVER** | no |
| `pmus-q1-executor` `fbd8eab5` | `feature/l5-transfer-backtest` | NEVER | no |

**No deployed service runs the patch.** `l5-pmus-live` is unchanged since
2026-10-09T02:12 with no staged changes, and `pmus-q1-executor` has **no
variables at all** — no credentials, so it cannot trade; last deployed
2026-09-06.

## 3. The five mechanisms compose `[CODE]`

`tests/test_guardian_full_chain.py` drives all five in one sequence across a
simulated process boundary against a single mutable fake venue: authenticated
pricing → gate clears → INTENT readable **from disk** before acknowledgement →
a second window refused while an order is outstanding → SIGTERM cancels and
**verifies**, with the venue filling in the cancel race (the R4 shape, verdict
`FILLED_NOT_CANCELLED`) → a fresh store on the same path recovers the fill,
books it **once**, reopens; repeat recovery books nothing.

Two negatives: an unreadable venue at restart keeps the chain **closed**
rather than concluding all-clear, and a stale feed refuses independently of
the gate. **199 passed** across all guardian/gate/canary/oneshot suites.

### The finding that changes the deployment plan `[VENUE]`

**`l5-pmus-live` has no volume mounted** (`"volumes": []`). ALWAYS-restart
delivers automatic recovery only if the ownership store **survives** the
restart, which requires a volume. So the two properties crash recovery needs
are split and **neither service has both**:

| | durable volume | automatic restart |
|---|---|---|
| `l5-live-probe-oneshot` | **yes**, 50 GB at `/state/pmus-ws` | no (NEVER) |
| `l5-pmus-live` | **no** | yes (ALWAYS) |

This is why the recommendation is the one-field change on the **oneshot**,
not on `l5-pmus-live`: the oneshot already has the volume. Adding a volume to
`l5-pmus-live` is the worse option — the project was previously at its
20-volume ceiling.

## 4. The October 7 fill — outcome resolved, cashflow NOT confirmable

`CYKE4XSQ0YGW`, DOWN 0.01 @ own 0.67, cost **$0.0067** `[VENUE]`.

`[INFERRED]` The local full-depth tape carries that window to termination:
`cpc-btc-updown-15m-2026-10-07-0200z`, final state `MARKET_STATE_CLOSED`,
last trade **0.0100** on the long side — so UP settled ~worthless and **DOWN
won**. Our DOWN position therefore pays **$0.0100**, implying **+$0.0033
gross**. Basis is `SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK`, **not** a venue
`settlement_px` read.

**The cashflow cannot be confirmed**, for three independent reasons:
1. the probe's own record says `settlement: observed: false` and
   `final_net_usd: UNRESOLVED_UNTIL_CASH_CONFIRMED` `[VENUE]`;
2. no follow-up record for that order or slug exists after 02:05 — it was
   never reconciled by us;
3. isolating **$0.0033** needs a balance pair bracketing only that
   settlement, and no such pair exists: other probes and windows ran in
   between.

What **is** confirmed `[VENUE]`: buying power **243.14705**, identical across
three independent preflight reads (19:03, 19:18, 19:32 on 2026-10-09), and
`no_unexpected_open_orders: 0` in all three. **No residual order exposure.**

## 5. Disarmed deployment and rollback plan

**Target:** `l5-live-probe-oneshot` `84aa9999`. **Nothing about
`l5-pmus-live` changes.**

**Pre-deploy**
1. Confirm `l5-pmus-live` still reads `armed: false` / `owner_arm_token:
   false` / `live_orders: 0`.
2. Record the rollback pin: **`b88ec5eec`** (current), plus
   `restartPolicyType: NEVER`.
3. Confirm `PMUS_Q1_ARM` / `PMUS_EXEC1_ARM` / `PMUS_CANARY_ARM` on the
   oneshot are unset or non-matching. **Set no arm token.**

**Deploy — disarmed, in this order**
4. Point the oneshot's source at `claude/l5-pmus-economic-results-7c116e`
   pinned to **`3dc9f135c`**. (Branch switch: it currently tracks the
   research branch.)
5. Leave `L5_GUARDIAN_STATE` unset → the store defaults to
   `/state/pmus-ws/guardian_ownership.json`, on the existing volume.
6. Start command unchanged. Expect `L5_LIVE_DISARMED` and
   `startup_reconciliation` in the journal, `may_submit` true on a clean
   venue, and **zero** `POST /v1/orders`.
7. Verify from logs: the reconciliation record is present, no order was
   created, and the guardian file exists on the volume.

**The restart-policy change — SEPARATE, and not part of this deployment**
8. `restartPolicyType: NEVER → ALWAYS` on `84aa9999`, one field. **Needs its
   own authorization.** Only after step 7 is verified.

**Rollback** — any step, in under a minute, no data loss
- re-pin the source to `b88ec5eec` on the research branch;
- restore `restartPolicyType: NEVER` if step 8 was taken;
- the guardian file is additive: it is read only by code that no longer runs
  after rollback, and deleting it is unnecessary. Nothing else on the volume
  is written by this patch.
- **No venue state changes**, because nothing is armed and no order exists.

**Abort conditions:** any `POST /v1/orders` in the logs; `SubmissionBlocked`
on a venue we believe clean; the guardian file absent after a run; any
`armed: true`.

## 6. GO / NO-GO

**GO — disarmed safety deployment of `3dc9f135c` to `l5-live-probe-oneshot`.**
It cannot place an order (no arm token, and `guardian_required()` → the gate
raises before any submission), the regression diff is clean (85 → 50, zero
regressions), the image closure is fixed and asserted, and rollback is a
one-field re-pin.

**NO-GO — arming anything.** Safety-critical blockers:

1. `[BLOCKED]` **`pmus_q1_executor` has the same unattached pricing boundary**
   and 9 of its safety assertions never execute. Fixing it is the same
   one-line pattern, but it is a change to the live executor and belongs in
   its own reviewed patch.
2. `[BLOCKED]` **No crash recovery until the restart-policy change.** The
   store is durable but nothing restarts the oneshot after SIGKILL/OOM.
3. `[BLOCKED]` Category B's 12 failures guard arming for other entrypoints.
4. `[VENUE]` Maker role unprovable (no `isAggressor`); queue position
   unobservable; no client order id, so lost-ack identity is heuristic.

**My recommendation:** deploy disarmed, verify step 7, then authorize the
restart-policy change, then let me fix the executor's pricing boundary under
review. Arming should wait for all three.
