# L5 EXPERIMENT REGISTRY

**New file** at commit `0bf8f0ee`+; no prior version existed. Append-only ledger
of experiments and the mandatory PRIOR ART CHECK that must precede each one.

Canonical findings: `L5_EMPIRICAL_MODEL.md` / `analysis/l5/l5_empirical_model.json`.

## The PRIOR ART CHECK (mandatory, before any new experiment)

No experiment starts until all six are answered in writing here:

1. **Does a canonical finding already answer it?** If yes, cite the ID and stop.
2. **Has this been run before on this branch or another?** Search the repo and
   the experiment table; a re-run needs a materially new hypothesis, not a
   re-read.
3. **Is the evidence class sufficient to answer it at all?** State the data
   requirement. If the data does not exist, the answer is "not sourceable", not
   a modeled guess.
4. **Does it require a parameter, threshold, cap or lane change?** If yes it is
   not an experiment, it is a proposal, and it needs preregistration first.
5. **What would falsify it?** Predeclared.
6. **What is the labelling discipline?** EXPECTED / ELIGIBLE / CREDITED /
   REALIZED kept separate; modeled numbers never enter net.

## Experiment log

### EXP-001 — ECONOMIC_COMPLETE conservation/causality audit
**Status: COMPLETE.** Commits `1ea8760e`, `3a92c583`, `7c11bc23`.
Found and fixed a double-charged completion taker fee; replaced the queue-ahead
measure; falsified the stale-order hypothesis (ATOMIC_RECYCLE no-op).
Feeds L5_ECON_00x supersessions and PMUS_ECON_001.

### EXP-002 — five-window frozen scoring
**Status: COMPLETE.** Commit `19955307`. n=5, 3 UP / 2 DOWN, 630 configurations
reconciled. Feeds PMUS_ECON_001.

### EXP-003 — L5 advantage decomposition
**Status: COMPLETE.** Commit `0bf8f0ee`. Feeds L5_ECON_001/002/003,
PMUS_ECON_002, RESIDUAL_001/002.

### EXP-004 — PMUS INCENTIVE REALITY CHECK
**Status: PRIOR ART CHECK PASSED — OPEN, evidence gathering.**

Prior art check:
1. **Already answered?** No. PMUS_ECON_003 establishes that *credited* PMUS
   incentives are **$0**, and PMUS_ECON_002 establishes that missing incentives
   are 74.7% of the q25 gap. Neither says what the venue's program *offers* or
   whether we are eligible. That is the open question.
2. **Run before?** No. `D_INCENTIVE_STATUS = "UNTESTED_NOT_SOURCEABLE"` in
   `src/l5_complement_response_curve.py` refers to *Polymarket's* historical
   crypto reward schedule, a different venue and period. Prior PMUS work
   established the $0 credited fact but never characterised the program terms.
3. **Sufficient evidence class?** Requires first-party PMUS documentation, API
   responses, and our own account credit history. Where a term is not published
   or not returned by the API, the answer is recorded as NOT SOURCEABLE.
4. **Parameter change?** No. Read-only characterisation. Any capital increase is
   a separate proposal and is explicitly deferred until the economics are
   returned.
5. **Falsification:** the check fails if published maker-rebate terms plus our
   observed queue-aware turnover cannot reach the ~+$0.57/window (q25) needed to
   clear zero, or if we are ineligible.
6. **Labelling:** EXPECTED (formula × our volume), ELIGIBLE (program says we
   qualify), CREDITED (venue paid), REALIZED (settled cash). Kept separate;
   nothing modeled enters PMUS net.

Constraints: no deposit, no live order, no deployment, no infrastructure change.
Return incentive economics **before** proposing any capital increase.

### EXP-005 — RESIDUAL_002 mechanism diagnostic
**Status: NOT STARTED.** Highest-value open diagnostic per RESIDUAL_002. Should
characterise PMUS residual by acquisition-price band against L5's band
distribution. Blocked on more usable windows (n=1 terminal residual episode).

## Standing frozen items

- θ = 0.96 (THETA_001). The θ=1.02 n=5 reading is recorded, not selected.
- q grid, continuation variants, DEV/OOS split, completion rules: preregistered
  and unchanged.
- ATOMIC_RECYCLE: falsified/no-op, default OFF, not a variant.
- PMUS_Q1_ARM empty. No live orders. Loss budget untouched.
- Checkpoint 25 remains the preregistered economic checkpoint (currently n=5).

### EXP-004 — PMUS INCENTIVE REALITY CHECK
**Status: COMPLETE.** Commit `8bc089b4`, `analysis/pmus/PMUS_INCENTIVE_REALITY_CHECK.md`.
Findings PMUS_INC_001/002. Forced an amendment to PMUS_ECON_003 (credited maker
rebates are +$2.13, not $0) and a scope narrowing of PMUS_ECON_002.

### EXP-006 — RESIDUAL_002 mechanism (supersedes EXP-005)
**Status: COMPLETE.** Resolved analytically from `E = w − b`, no new data needed.
RESIDUAL_002 → RESOLVED; RESIDUAL_003 records the causal mechanism (EC retires the
winning leg). No rule added.

## DO NOT RETEST — falsified or resolved

| idea | verdict | why |
|---|---|---|
| ATOMIC_RECYCLE / cancel stale orders after completion | **FALSIFIED, no-op** | no stale passive orders exist; residual is 100% from new decisions (`7c11bc23`) |
| B′ baseline as the path to ≥$5/window | **FALSIFIED** | net is unhedged directional residual on 3-of-4 episodes; repriced at L5's measured 0.3540 win rate it is −$10.14 (q25) / −$20.28 (q50). BPRIME_001. |
| θ=1.02 (or any θ) selected from the n=5 sweep | **FORBIDDEN** | THETA_001; sign flipped between n=2 and n=5 |
| "PMUS residual per unit is worse than L5's, so our execution is worse" | **RESOLVED, false** | a losing residual loses exactly its basis; decomposes to band concentration + one draw. RESIDUAL_002. |
| "credited PMUS incentives are $0" | **CORRECTED** | maker rebates +$2.13 over 293 fills are credited. PMUS_ECON_003 amended. |
| Applying L5's 0.919%/$ incentive rate to PMUS as achievable | **NOT AVAILABLE** | BTC15m is in no liquidity program; achievable is maker-rebate-only 0.396%. PMUS_INC_001. |
| Simultaneous YES+NO<$1 arbitrage on this tape | **FALSIFIED** | fresh two-sided pair cost is 1.01–1.03 at every completion instant, never below $1 |

### EXP-007 — rolling money accounting to checkpoint 25
**Status: OPEN, passive.** Per-window ledger: matched gross → taker fees →
matched net → terminal residual → credited incentives → REALIZED-equivalent.
Rolling n=5 available; **n=10/25/50 not yet available** (only 5 usable windows
exist). Hourly routine continues under the frozen policy. No action until n≥25.

---

## AMENDMENT: the premature-rejection lesson

EXP-003/EXP-004 concluded "no executable path to ≥$5/window." The adversarial
reanalysis (EXP-008) showed that conclusion was **overstated at n=5** because the
residual term was a single observation whose CI spans the target. Two windows of
new data moved q25 from −0.5682 to +0.1067. **The rejection is withdrawn.**

Registry rule added: **a rejection may not be recorded from a term whose
confidence interval spans the decision threshold.** State "not established"
instead.

### EXP-008 — adversarial reanalysis
**Status: COMPLETE.** Amended TARGET_001 to NOT ESTABLISHED OR EXCLUDED; added
MATCHED_001 (allocation-convention band ±$0.80/window at q50) and CAPACITY_001
(q>5.31 is modeled). Confirmed no double counting arithmetically. Found and fixed
a reporting-helper defect that overstated REALIZED-equivalent by the fee term
(`a26269a8`).

### EXP-009 — P0: terminal-residual frequency and severity (SOLE P0)
**Status: OPEN.** Collect and score to **n=25 minimum** under the frozen policy.
Ledger regenerated every firing by `scripts/pmus_window_ledger.py` into
`analysis/pmus/pmus_window_ledger_q{25,50}.json`.

Per-window preserved: terminal residual occurrence · residual contracts ·
residual basis · side · settlement · residual P&L · matched contracts ·
matched gross · fees · expected maker rebate · total net.

Rolling n=5/10/15/20/25 maintained. **Collection does not stop because an
intermediate checkpoint looks good or bad.**

At n=25: **ADVANCE TO BOUNDED LIVE CAPACITY TEST** or **BTC15M ECONOMICS
REJECTED**. Advancement requires a credible path to ≥$5/window with no
directional prediction, no θ tuning, no hypothetical liquidity rewards and no
unsupported linear scaling.

### EXP-010 — capacity experiment (NOT STARTED, required eventually)
q=50 is **9.42×** the largest clip ever filled live (5.31 contracts). Until a
capacity experiment demonstrates fills above ~5 contracts, every q>5 economic
figure is modeled. Design deferred; no orders, no deposit.

## DO NOT RETEST — additions

| idea | verdict | why |
|---|---|---|
| "BTC15m economics are rejected" (as of n=5) | **WITHDRAWN — premature** | residual CI spanned the target; n=7 moved q25 positive. TARGET_001. |
| Treating q25/q50 as demonstrated executable capacity | **FALSE** | max live fill is 5.31 contracts; q25 is 4.71×, q50 9.42×. CAPACITY_001. |
| Quoting matched gross without its convention band | **INCOMPLETE** | ±$0.80/window at q50 between average-cost and FIFO. MATCHED_001. |
| q=25 as a route to $5 | **effectively excluded** | needs residual frequency 0.0615, below the Wilson lower bound 0.1582. P0_RESIDUAL_001. |

### EXP-010 — bounded live capacity/economics probe (PROBE-CAP-02)
**Status: AUTHORIZED, NOT STARTED. $0.00 of $5.00 spent.**
Ledger: `analysis/pmus/PROBE_AUTHORIZATION_LEDGER.md`.

Budget PROBE-CAP-02 ($5.00 realized-loss cap) is separate from RESEARCH-LOSS-01
($0.49320, untouched). Hard stop −$5.00, never raised to finish a sequence.

Blocked on three findings, all established read-only before any arming:
1. No PMUS credentials in this container **or** on the `pmus-q1-executor` service
   (only Railway's 8 built-ins; no sealed variables), so "no orphan orders or
   positions" **could not be verified** — the mandatory precondition.
2. Arming is dual-key operator-only by design; the module says Claude never arms it.
3. **The existing executor is hard-capped at q=1**, $1.00 campaign risk, 1-contract
   residual, $1.00 daily realized-loss stop — constants, not knobs. It cannot
   express a q5→q10→q25 ladder, and its $1.00 stop conflicts with the $5.00
   envelope.

Also: the deployed executor builds from `feature/l5-transfer-backtest` and last
deployed 2026-09-06, predating `1ea8760e` (double-fee fix) and `3a92c583` (queue
model). Any live run should be rebuilt from current HEAD first.

Nothing was armed, no variable was set, no order was placed.

### EXP-011 — read-only live-architecture engineering
**Status: COMPLETE.** `analysis/pmus/EXP010_ENGINEERING_READONLY.md`.
Established the post-breach architecture, verified GuardedTransport covers
COMPLETE/complement by construction, confirmed both 09-16 faults are already
pinned by existing tests (77 passed), found the taker-completion primitive already
exists (`micro_complement_taker_*`, `post_only=False`, gated by
`taker_completion_ok`), and labelled the 5.31 fill as tennis evidence.

## DO NOT RETEST / DO NOT USE — additions

| item | verdict | why |
|---|---|---|
| `src/pmus_l5_ramp_ladder.py` owner-rung mechanism for LIVE authorization | **DO NOT USE FOR LIVE AUTHORIZATION** | fault (1) of the 2026-09-16 breach: persisted historical rung approvals were auto-loaded by a running campaign and silently replaced live limits ($0.05→$7.50, −$1.00→−$15.00). RAMP_LADDER_001. Live authority is the immutable mandate only. |
| PROBE-CAP-02 as an independent fresh $5.00 budget | **FALSE, CORRECTED** | one cumulative state is authoritative: −$4.50680000 of $5.00, **$0.49320 remaining**. BUDGET_001. Rung-A's $1.50 is NOT AUTHORIZED. |
| "matched economics are positive at every clip and fill model" | **FALSE at n=9** | θ gates completions, not passive acquisitions; a whipsaw produced a 1.3500 pair cost at 2030z. MATCHED_002. |
| Treating the 5.31 fill as BTC15m capacity evidence | **FALSE** | ATP tennis markets, 2026-09-07. Zero authenticated live BTC15m fills exist. |

### EXP-012 — matched-unit mechanism decomposition (n=10 checkpoint)
**Status: COMPLETE, diagnostic only, gates nothing.**
`scripts/pmus_matched_decomposition.py` →
`analysis/pmus/pmus_matched_decomposition_q{25,50}.json`.
Findings MATCHED_003, PAIRCOST_001, WHIPSAW_001, EXPOSURE_001; P0_RESIDUAL_001
updated to n=10. Policy, θ, fill model, q grid, DEV/OOS split and the
preregistered decision rule are **unchanged**.

Two defects in my own new diagnostic, found and fixed before any number was read:
1. the reconciliation fields were assigned **after** `json.dump`, so they never
   reached the files, and the "no mismatch printed" from the first pass was
   **vacuous** — there was no longer a print to emit one;
2. the EC tie check was written `abs(ec - (matched - (matched - ec)))`, an
   identity that can never fail.
Now: reconciliation runs before the write, the EC tie is a real `assert` against
the scorer's `gross_margin` (passes to 1e-9 in all 10 windows), and the passive
convention delta is printed and persisted per window.

## DO NOT RETEST / DO NOT USE — additions

| item | verdict | why |
|---|---|---|
| "the 2030z 1.78 pair is a rule defect to be gated" | **NOT ESTABLISHED** | the pair cost is an accounting relabel of an already-doomed residual; the late complement buy was **+$0.50 better** than settling the residual. WHIPSAW_001. |
| "L5 would not have opened DOWN at 0.79 there" | **FALSE** | movement-driven side selection is STRONGLY SUPPORTED, `LEG_PATTERN` has an explicit 0.800 first-leg band, and 75–90c is L5's **best** residual band (+0.1382/unit). WHIPSAW_001. |
| reading residual frequency 0.30 as "directional risk occurs 30% of the time" | **FALSE** | peak one-sided exposure occurred in **10 of 10** windows; 0.30 is the rate at which it failed to flatten by expiry. EXPOSURE_001. |
| the FIFO passive matched figure as a P&L number | **DO NOT USE** | it is path pricing; per-window convention delta reaches −25.3611 at q=50. Use `passive_matched_gross_avg_cost`. MATCHED_003. |

### REPO_TESTS_001 — base and HEAD have IDENTICAL failures
Measured, not inferred, on 2026-09-27. Full suite excluding five files whose
imports are missing optional deps (`dotenv`, `httpx`; collection is otherwise
clean at 9434 tests):

| | base `119360b0` | HEAD |
|---|---|---|
| failed | 40 | **40 — the same 40 ids** |
| errors | 23 | **23 — the same 2 files** |
| passed | 9343 | 9343 |
| skipped / xfailed | 27 / 1 | 27 / 1 |

The two `FAILED` id sets diff to **zero only-at-HEAD and zero only-at-base**, so
nothing in the n=10 checkpoint work caused any of them — including the `analysis/`
artifacts written this session, which `test_r1_source_lineage`,
`test_window_forensics_stream` and `test_lane_a_probe_v0` read.

Failures: `test_gemini_terms_gate` 17, `test_orderbook` 5,
`test_r1_source_lineage` 4, `test_deploy_fallthrough_guard` 3,
`test_lane_a_probe_v0` 2, `test_gemini_settlement_observability` 2, and one each
in `test_window_forensics_stream`, `test_pmus_ws_evidence_recorder`,
`test_pmus_q1_executor`, `test_l5_live_oneshot`,
`test_gemini_write_path_isolation`, `test_gemini_payload_schema`,
`test_chain_ca_fallback`. Errors: `test_pmus_status_live_handoff` 14,
`test_gemini_request_transport` 9.

**Not diagnosed now, by instruction — collection is P0.**

#### BLOCKING CONDITION ON ANY FUTURE LIVE AUTHORIZATION
**Before any live authorization, the EXP-010 execution-path failures must be
RESOLVED or EXPLICITLY PROVEN IRRELEVANT to the authorized artifact**, per owner
instruction 2026-09-27. Named, so the list cannot be quietly narrowed later:

`test_pmus_q1_executor` · `test_deploy_fallthrough_guard` ·
`test_lane_a_probe_v0` · `test_l5_live_oneshot` · `test_r1_source_lineage` ·
`test_pmus_status_live_handoff` (14 errors) · `test_pmus_ws_evidence_recorder`

"Proven irrelevant" means the artifact that would run does not execute the code
under test, demonstrated against that artifact — **not** an argument that the
failure looks unrelated. The live-safety suites are green today
(`test_live_mandate_{deployment,adversarial,ledger_invariant}`,
`test_pmus_capacity_rung_manifest`: 58 passed) and that is **not** a substitute:
they bound what a spend may do, these cover whether the executor behaves.

### EXP-013 — checkpoint accounting shape (reporting only)
**Status: COMPLETE.** `scripts/pmus_window_ledger.py`,
`tests/test_pmus_shadow_net_identity.py` (20 tests).

`total_net` and `realized_equivalent` are **retired**. The second was
`total_net` plus a MODELED maker rebate while being named "realized", and no
published line was free of an incentive term at all. Replaced by the eight rows
below, with `fees` as a **positive cost magnitude** so both identities hold
literally, and an `accounting` block in each ledger JSON carrying the sign
convention, the identities and the EXPECTED-vs-CREDITED statement.

Identities enforced per window **and** at every rolling checkpoint:

    ec_matched_gross + passive_matched_gross    = matched_gross
    matched_gross - fees + residual_pnl         = shadow_trading_net
    shadow_trading_net + expected_maker_rebate  = shadow_modeled_net_...

`shadow_trading_net` is recomputed **from the scorer** in the test, so the suite
cannot pass by reading back its own arithmetic, and two negative controls confirm
teeth: folding the rebate into the trading line fails 3 tests, flipping the fee
sign fails 2. A row whose name contains "realized" or "credited" now fails.

Scorer, θ, fill model, q grid, decision rule and the n=25 preregistration are
**unchanged**; no number moved.

### EXP-014 — PMUS market resolution: two implementations, one venue route
**Status: COMPLETE (code consolidated, not yet deployed).** Evidence:
`analysis/l5/rung_a/PMUS_ENDPOINT_DISCOVERY_001.md`,
`tests/test_market_resolution_2215z_regression.py` (18 tests).

Written so this is never rediscovered.

**THE OLD WORKING PATH.** `pmus_micro_executor.market_micro_spec`, used by
`pmus_spring_l5_session` and `pmus_micro_executor` — the code that has
placed and settled real PMUS orders — resolves a market with **one call**:

    GET https://gateway.polymarket.us/v1/market/slug/{market_slug}
    -> resp["market"]["minimumTradeQty"], ["orderPriceMinTickSize"]

`pmus_q1_executor`, the original live executor, resolves **no** metadata: it
reads `/v1/markets/{slug}/bbo` for the book (`fresh_bbo`) and uses
`POST /v1/order/preview` as the pre-trade validator, exposing
`tick_for_market` over the same `orderPriceMinTickSize` field.

**THE LOCK5 PATH.** `l5_pmus_transport.resolve_market` was a SECOND
implementation: api host first, then `/v1/market/slug/`, then
`/v1/events/slug/{event}`, per host. Its 2026-09-24 docstring records that
it was adopted because `/v1/market/slug/` answered "not found" for BTC15m on
35 consecutive windows while `/bbo` served the same slug.

**WHAT IT COST.** 2026-09-28: the 2000z probe failed preflight with a null
fingerprint and no other evidence — three gates were consistent with it and
the `checks` list was only on an unreadable volume. The 2215z probe then
failed `market_readable` for a full window. The row it could not read had
been created by the venue at **10:40:01Z**, eleven hours earlier. Measured
at 22:34:59Z, `/v1/market/slug/` on the gateway served **every** BTC15m
window tested. The failure was a transient gateway 404 for 2200z/2215z
lasting roughly 22:15Z–22:31Z, inside the window, with no pre-window read to
fall back on.

**RESOLUTION.** One implementation, `src/pmus_market_row.py`, used by both.
Gateway only; the api host does not serve these public routes.
`/v1/events/slug/` demoted to `series_provenance`, non-raising, outside the
execution chain — it carries only `seriesSlug`, and
`market_series_is_btc15m` is satisfied from the slug. `/bbo` is **not** an
existence oracle: it 404s for every pre-open window and 404ed for 2215z
after that window had opened and closed.

**RESIDUAL RISK, STATED.** The 2026-09-24 evidence for the old route is not
disproved — it may be that `/v1/market/slug/` genuinely did not serve BTC15m
then. If it stops serving again, the single-route resolver has no fallback
and the probe **refuses the window** rather than trading on unproven
constraints. That is fail-closed and deliberate; the pre-window pin is what
converts a transient outage from fatal to survivable, and it cannot help a
slug that was never resolvable at all.

**Selection.** Slugs are no longer computed from the clock.
`pmus_book_tape.series_windows` (extracted from `series_slugs`, whose
behaviour is unchanged) returns the venue's own listing, and
`select_venue_window` picks the current window if ≥240 s remain, else the
next. The venue's `closed` flag cannot be used to choose: 639 events
measured 2026-09-28T22:52Z, reaching 2026-09-29T10:45Z, and exactly one had
`closed: false` — the window trading at that moment.

Scorer, θ, variant, clip, hold, residual cap, LOCK5 threshold, floor guard,
complement bias and the n=25 preregistration are **unchanged**; q50 parity
remains 25/25 exact at 6.1167.
