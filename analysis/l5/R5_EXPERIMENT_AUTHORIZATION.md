# R=5 frequency experiment — exact deployment and authorization

Status: **PREPARED. Nothing deployed, no variable set, no order armed.**

Thesis under test: additional completed pairs raise net profit while
unmatched inventory stays bounded. The projected +$0.11/window is a
**prediction to test**, used nowhere in this document.

## 1. The invariant, stated correctly

The directive asks to verify that "no more than one opening round can remain
unmatched". **That is not what the engine enforces, and asserting it would be
wrong.** What it enforces, verified in code and pinned by
`tests/test_r5_unmatched_invariant.py` (9 tests):

> **|PROSPECTIVE net unmatched| <= 3 contracts**, where prospective counts
> realized inventory PLUS every live order as if it filled in full.

With the opening clip capped at 3 contracts this bounds unmatched exposure at
3 contracts at all times — the economically relevant bound. Two *small*
unmatched legs may coexist provided their sum stays inside it, so the number
of unmatched rounds is not the quantity that is bounded; the contract count
is. The P&L model only ever used contracts, so it is unaffected.

`src/l5_live_execution.py:547` — `abs(residual_after + outstanding_residual)
> self.max_abs_residual`. `ABS_RESIDUAL_CAP_CONTRACTS = 3`, and
`cap = min(k * clip, 3)` at 6355 takes the intersection, never the wider.
Live preflight confirms `effective_cap_contracts: "3"`,
`which_binds: "ABSOLUTE"`.

**The ceiling is inclusive.** The test is `> cap`, so exactly 3.00 contracts
is permitted and 3.01 is refused. My first draft of the test asserted the
opposite and was wrong; the code is right.

### The four named cases

| case | mechanism | verified |
|---|---|---|
| **partial fills** | `outstanding_residual` sums `remaining`, not original qty, so the filled part lands in realized residual and the remainder in outstanding — never both, never neither. `live_order_count` treats `PARTIALLY_FILLED` as **non**-terminal (a previously fixed bug: `"PARTIAL" in "...FILLED"` had retired partials from the one-order bound). | test + code |
| **overlapping orders** | prospective net sums every live order in the market on both sides; this is the 1130z fix, where realized residual sat inside the cap while four DOWN orders filled within seven seconds and inventory reached 3.24 against a cap of 1.80. | test + code |
| **cancellation races** | a cancel returning `still_open` or `unverified` leaves the order in `live_orders` with `remaining` intact, so it stays counted. If we cannot tell, `orders_uncertain` is set and **every** further submission is refused `ORDER_STATE_UNCERTAIN`, checked before any arithmetic. | test + code |
| **restart reconciliation** | the live count includes `live_orders`, `pending_terminal` and `quarantine`; an unreadable `remaining` counts as live (`rem is None or rem > 0`), and `reconcile_verified_l5_net` reads the venue as authority. Nothing is dropped by a restart. | code |

One residual risk, stated rather than closed: `outstanding_residual` maps an
unreadable `remaining` to 0 contracts, so that row's exposure rests on the
live-order **count** and on `orders_uncertain` rather than on the contract
bound. Both are active; the test pins the behaviour so a change is visible.

## 2. Test parity

| | failed | passed |
|---|---:|---:|
| baseline (neither fix) | 20 | 13,770 |
| + reporting fix + floor fix | **20** | 13,790 |
| + invariant tests | **20** | **13,799** (expected) |

**Zero regressions.** The same 20 pre-existing failures and 14 pre-existing
errors throughout; the +29 passing are 12 reporting-fix, 8 floor-fix and 9
invariant tests.

## 3. Both fixes are in the deployable image

    deploy/l5_pmus_live/Dockerfile:54   COPY src/l5_decision_role.py
    deploy/l5_pmus_live/Dockerfile:103  COPY src/l5_two_sided_probe.py

27 image-closure tests pass. The `l5_decision_role.py` COPY is load-bearing:
the runner imports it at module scope, and without the line the entrypoint
raises ImportError at boot. Four closure tests caught that before deployment.

## 4. Exact proposed deployment

**Commits to deploy** (both prepared, tested, parity-verified, pushed):

    cb22bb47  first-leg floor keyed on the imbalance, not on holding anything
    c16ee7ec  decision-role reporting field + the Dockerfile COPY

**Variables to set on `l5-pmus-live`** — exactly two:

    L5_MAX_CASH_DEPLOYED_USD   6.00  ->  15.00
    L5_TS_ROUND_ALLOCATIONS       2  ->  5

**Variables to leave exactly as they are:**

    L5_MAX_TOTAL_EXPOSURE_USD           20     aggregate ceiling
    L5_TS_OPENING_LEG_MAX_CONTRACTS      3     opening clip
    L5_TS_MIN_FIRST_LEG_PRICE         0.20     first-leg floor
    pair-cost ceiling                 0.96     TS.max_pair_cost()
    L5_MAX_L5_DAILY_LOSS_USD             5     daily halt
    L5_MAX_CUMULATIVE_LOSS_USD          20     account-wide lifetime halt
    L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD  5     campaign-additional halt
    L5_MAX_WORST_CASE_DEFICIT_USD      3.00
    L5_MAX_LIVE_ORDERS                   1     plus the existing pair slot
    L5_MAX_UNMATCHED_CONTRACTS           5
    PMUS_RESIDUAL_CAP_K             unchanged  (binds ABSOLUTE at 3)
    L5_HALT_URL / watchdog          unchanged
    execution mode                 MAKER_ONLY

No new service, no taker execution, no clip increase, no looser cap, no
change to any cutoff or runway gate.

**Deployment timing.** The rollout must land in the **T+540 to T+600** band
of a window, per `analysis/l5/DEPLOYMENT_TIMING_SAFEGUARD.md`. Three
rollouts cost two claimed windows this session.
`python3 scripts/l5_rollout_window_check.py` returns GO/NO-GO and exits
non-zero on NO-GO. Run it immediately before applying.

## 5. Campaign authorization

Emitter: `scripts/l5_r5_frequency_approval_emit.py` (prints a document;
authorizes nothing).

    nonce      4f1b25fe655d20d53ca8d698        (fresh, 24 hex)
    nonce_sid  camp1.4f1b25fe655d20d53ca8d698  (starts its own 0/8 count)
    series     cpc-btc-updown-15m-
    max_windows             8
    max_total_exposure_usd  20
    max_daily_loss_usd      5
    duration                3h, clamped by state() to issued + 4h

The exhausted `camp1.eb79a4b271122db56669c089` (8/8, realized **-$1.4140**)
is left untouched as the audit record. It is neither reset nor extended.

**Re-run the emitter at activation time.** `issued_utc` / `expires_utc` are
stamped when it runs and the staleness gate is 4h, so a document minted now
and pasted hours later would be refused. The sha256 changes with the
timestamps; the nonce does not.

A campaign BOUNDS a run and does not authorize one. Arming remains a separate
handshake: `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` must match and
`L5_LIVE_AUTH_NOT_AFTER` must be refreshed.

## 6. What will be measured, and what would stop it

Per window, from venue-confirmed settlement only:

- completed rounds and completed pairs
- total realized net P&L, matched and residual reported separately
- residual exposure in contracts at window end, against the 3-contract bound
- pair cost per completed pair, and time from first leg to complement
- `decision_role` on every decision, so NEW_OPEN and COMPLEMENT are separable
  without `legs_claimed`

**Stop conditions, declared in advance:**

1. Unmatched contracts exceed 3 at any point — the invariant is broken.
2. The strand count scales with R instead of staying near 1 — the model is
   wrong. This is the load-bearing assumption, validated on only **two**
   windows.
3. Either loss halt latches.
4. Eight windows consumed. No automatic extension.

**No capital increase is requested.** The $150-300 headroom that $5/window
needs ($107-138 of deployed cash) should be revisited only after R=5
produces repeatable positive **settled** net across these eight windows.

## Authorization requested

1. Deploy `cb22bb47` and `c16ee7ec`.
2. Set `L5_MAX_CASH_DEPLOYED_USD=15.00` and `L5_TS_ROUND_ALLOCATIONS=5`.
3. Paste a freshly re-run approval document for nonce
   `4f1b25fe655d20d53ca8d698` into `L5_CAMPAIGN_APPROVAL`.
4. Refresh `L5_LIVE_AUTH_NOT_AFTER` and complete the arming handshake.

Items 1-3 are preparation and bounding. **Item 4 is the only step that can
put an order on the wire**, and I will not take it without explicit
authorization.

---

# Pre-arming verification: two blockers found

## Check 1 — parity: PASS

Baseline 20 failed / 13,770 passed. With the reporting fix, the floor fix
and the nine invariant tests: 20 failed / 13,799 passed, 0 regressions.
With the fail-closed fix of check 2 and its two further tests:
**20 failed / 13,801 passed, 0 regressions**. Same 20 pre-existing failures
and 14 pre-existing errors throughout.

Worth stating explicitly: making `outstanding_residual` and
`outstanding_by_market` STRICTER broke no existing test. A more conservative
exposure bound could easily have turned previously-passing gate tests into
refusals, and it did not, so the money path tolerates the tighter bound.

## Check 2 — unreadable remaining: FAILED, now FIXED

It did **not** fail closed. `outstanding_residual` and
`outstanding_by_market` both mapped an unreadable `remaining` to **0
contracts** (`venue_decimal(...) or D(0)`), while `live_order_count`
correctly counted such a row as live. So a row we could not size contributed
nothing to the residual contract bound yet still occupied an order slot and
still let the pair slot open: the 3-contract bound could be understated by a
whole clip.

The same method already substituted `UNKNOWN_PRICE_WORST_CASE` for an
unreadable **price**, on the stated principle that an unreadable row is not
an absent one. The quantity used the opposite convention.

`InventoryStore.worst_case_remaining` now prefers the readable `remaining`,
then the row's own ordered quantity (`qty` / `quantity` / `original_qty` /
`ordered_qty`), then `ABS_RESIDUAL_CAP_CONTRACTS = 3` — so the bound is
always at least at the ceiling that refuses a further opening. Both call
sites use it. A readable zero still means zero. Four added tests cover the
null, the corrupt string, each quantity fallback, the notional path, and the
end-to-end refusal. Invariant tests now 11.

## Check 3 — deployment health: BLOCKED, cannot be done as asked

The service builds from a **different branch** than the one this work is on:

    service source   branch claude/l5-narrow-campaign-prep
                     commitSha ff1f8ea3  (PINNED)
    watchPatterns    ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]
    our commits      claude/l5-wallet-ingestion-fix-kqcqoa

So `cb22bb47`, `c16ee7ec` and the fail-closed fix are **not reachable by the
deployed service**. Making them reachable needs one of:

1. merging/pushing them onto `claude/l5-narrow-campaign-prep` — which my
   standing instructions forbid without explicit permission ("NEVER push to
   a different branch without explicit permission"); or
2. repointing the service source to `claude/l5-wallet-ingestion-fix-kqcqoa`
   — a service-config change.

Either way the pinned `commitSha` must be moved and
`deploy/l5_pmus_live/DEPLOY_TRIGGER` touched, since a code change alone does
not match the watch pattern. **I have not done any of this.** There is no
new deployment to verify, so check 3 cannot pass yet.

## Check 4 — loss and exposure gates: PASS, but they expose a conflict

Read from the running process at 22:46:25Z:

    max_cumulative_loss_usd        20       account-wide lifetime
    max_l5_cumulative_loss_usd     20
    max_l5_daily_loss_usd           5
    max_worst_case_deficit_usd   3.00
    max_unmatched_contracts         5
    max_cash_deployed_usd        6.00       (the one proposed to change)
    cash_policy            NO_RECYCLING
    halt_gate_configured         true
    campaign budget_usd         29.30       peak_usd 19.9451

All effective. **But the campaign worst-case budget conflicts with the
proposed $15 envelope.** `l5_live_validation_gates.py:694`:

    campaign_used = max(0, -cumulative_realized)
                    + cash_committed(filled_legs)
                    + pending_obligation(outstanding)

gated as `campaign_used + this_order > budget_usd` ($29.30), with
`cumulative_realized` the **account-wide** figure, now **-$15.0545**.

    usable headroom   29.30 - 15.0545 = $14.2455
    per round         $2.99 (measured)
    max rounds        4.76  ->  R = 4

| envelope | R | projected worst case | verdict |
|---------:|--:|---------------------:|---------|
| $15.00 | 5 | $30.0545 | **REFUSED** `CAMPAIGN_WORST_CASE_BUDGET` |
| $14.00 | 4 | $29.0545 | OK |
| **$12.00** | **4** | **$27.0545** | **OK, with margin** |
| $9.00 | 3 | $24.0545 | OK |

So **R=5 at $15 cannot run** without raising `L5_CAMPAIGN_BUDGET_USD`, which
is a loss-budget increase and is not requested. R=4 fits. Note the headroom
grows automatically as realized losses shrink, since the term is
`max(0, -cumulative_realized)`.

## Check 5 — campaign reissue: ready, deliberately not yet issued

Nonce `4f1b25fe655d20d53ca8d698`, scope verified: series
`cpc-btc-updown-15m-`, `max_windows 8`, `max_total_exposure_usd 20`,
`max_daily_loss_usd 5`, 3h clamped to 4h by `state()`. The emitter must run
at activation because the staleness gate is 4h, so issuing it now would
guarantee a stale document. The exhausted
`camp1.eb79a4b271122db56669c089` (8/8, realized **-$1.4140**) is untouched.

## Revised recommendation

**R=4, not R=5.** `L5_MAX_CASH_DEPLOYED_USD = 12.00`,
`L5_TS_ROUND_ALLOCATIONS = 4`. That is the largest frequency the existing
loss budget permits, and it is the modelled break-even (+$0.018/window)
rather than +$0.113. It still tests the thesis — whether pairs scale with R
while unmatched inventory stays bounded — which is what the experiment is
for. I would rather run the honest R=4 than request a loss-budget increase
to reach a projection.

**Aggregate exposure stays $20. The previous campaign is not reset.**
