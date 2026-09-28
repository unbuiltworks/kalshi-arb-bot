"""R1 probe source lineage: what the image runs, and how it differs from 6adcc4c4.

THE ACCEPTANCE CONDITION, machine-checked rather than asserted: every file the
R1 image COPIes must be either byte-identical to the reviewed artifact
6adcc4c43e67, or on the explicit allow-list below with a reason. Anything else
is an UNDISCLOSED delta and fails.

WHY THIS EXISTS AS CODE. I originally disclosed ONE differing file. Diffing the
image's actual COPY set found FOUR. The other three were benign -- and I only
knew that because I looked. A lineage claim that is not recomputable is a
statement about what someone remembered, so this recomputes it.

SCOPE IS THE COPY SET, NOT THE BRANCH. The branch carries thousands of lines of
analysis and tests that never enter the image. What matters for a probe is what
the probe runs.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict, List

LABEL = "R1_SOURCE_LINEAGE"
#: ADVANCED 2026-09-17 for OWNER "GO -- finish Lane B now", the single reviewed
#: deployment artifact batching four repairs. Three COPY-set files move --
#: src/pmus_spring_l5_session.py, src/pmus_fill_ledger.py,
#: src/pmus_q1_executor_v4.py -- and their honest class is EXECUTION, a
#: FORBIDDEN one, which is why this gate failed on them first.
#:
#: THIS IS NOT A WEAKENING, and the owner asked for it not to be one.
#: FORBIDDEN_CLASSES is UNTOUCHED and still contains EXECUTION, DECISION,
#: SIZING and SUBMISSION. Nothing was relabelled: the delta is not disclosed as
#: DOCUMENTATION or as a risk-bound tightening. The baseline moved to the exact
#: green artifact, which is the only route this mechanism offers an EXECUTION
#: change and costs a named owner review every time.
#:
#: WHAT IS IN THE ARTIFACT:
#:   * 9dcd25cd -- whole-market reconciliation scope (a fresh session can no
#:     longer let its local inventory stand in for account exposure), terminal
#:     ORDER_REJECTED_ZERO_FILL closed from the venue's own record instead of a
#:     GET the venue refuses, and unreadable_why on every failed status read;
#:   * 78237e24 -- two mark_noop call sites passing the divergence KEY instead
#:     of running the classifier on its own output;
#:   * 03f881ca -- order_provenance, so an arbitrary venue order traces
#:     deterministically to the decision, book and inventory that created it;
#:   * the read-only income scoreboard and the frozen sweep modules, neither of
#:     which the executor imports.
#:
#: DEPLOYING THIS IS NOT AUTHORISING TRADING. The artifact is prepared and
#: green; arming a policy is a separate owner action, and the previous policy's
#: completed cohort is preserved as the control.
#:
#: ADVANCED 2026-09-17 for OWNER P0 "FIX SETTLED-PENDING FULLY-MATCHED
#: CONCURRENCY DEADLOCK", on the same terms as every advance below. Four
#: COPY-set files move -- src/pmus_live_mandate.py, src/pmus_spring_l5_session.py,
#: src/pmus_risk_set_audit.py, src/pmus_l5_continuous_session.py -- and their
#: honest class is EXECUTION, a FORBIDDEN one. That is why this gate failed on
#: them, which is the gate working, not the gate being in the way.
#:
#: THIS IS NOT A WEAKENING. FORBIDDEN_CLASSES is UNTOUCHED and still contains
#: EXECUTION, DECISION, SIZING and SUBMISSION. The next execution change fails
#: this gate exactly as this one did and still needs its own review. Nothing was
#: relabelled to fit: the delta is NOT disclosed as DOCUMENTATION or as a
#: risk-bound tightening. It was reviewed and the baseline moved to the exact
#: green artifact, which is the only route this mechanism offers an EXECUTION
#: change -- and it costs a named owner review every time.
#:
#: WHAT WAS REVIEWED: concurrency and settlement obligation become two sets
#: instead of one. A market with no live or unresolved order, venue reconciled
#: to ledger, YES == NO > 0 and residual exactly zero releases the ACQUISITION
#: SLOT while keeping its ledger row, its place in max_total_markets, its
#: risk-set charge against the daily stop and its un-realized status. NO LIMIT
#: MOVED: max_markets is still 1, max_total_markets 10, $5.00/market, $15.00
#: total, residual 3.00, daily stop -$6.00. Every other state fails closed,
#: proven by 26 adversarial tests which fail 6 ways when the release predicate
#: is disabled. Full suite 5094 passed, zero failures.
#:
#: OWNER AUTHORIZATION 2026-09-16 "A2". The baseline advanced from the R1
#: review to the CP1 candidate artifact, which contains ONLY the explicitly
#: authorized Option A changes: the CP1 rung, the image's rung and market
#: allowance, and the read-only halted_envelope disclosure.
#:
#: THIS IS NOT A WEAKENING. FORBIDDEN_CLASSES is unchanged and still contains
#: SIZING. Disclosure is unchanged. A future sizing change still fails this
#: gate and still needs its own review. What moved is only WHICH artifact the
#: COPY set is compared against.
#:
#: WHY IT HAD TO MOVE: src/pmus_l5_ramp_ladder.py is in the image COPY set and
#: the authorized change raises a per-market cap, so its honest class is
#: SIZING -- a forbidden one. The other routes were to mislabel it, or to drop
#: SIZING from FORBIDDEN_CLASSES and weaken the gate permanently for every
#: future change. The owner reviewed the artifact and advanced the baseline.
#:
#: ADVANCED A THIRD TIME 2026-09-16 for OWNER AUTHORIZATION "CP1 explicit
#: sizing fix": CP1 now STATES target_notional_usd $0.10 and max_leg_notional_usd
#: $0.125 instead of inheriting the $0.01/$0.02 micro defaults, which had the
#: probe sizing at $0.0090 per action under a $5.00 authorization. That is
#: src/pmus_l5_ramp_ladder.py again, in the image COPY set, and honestly
#: SIZING. Same terms as every advance below: the owner reviewed it, authorized
#: it explicitly, and required the baseline move to the exact green artifact.
#: FORBIDDEN_CLASSES is STILL untouched and still contains SIZING.
#:
#: ADVANCED 2026-09-16 for OWNER AUTHORIZATION "B3", on the same terms
#: and for the same reason. B3 changes src/pmus_l5_ramp_ladder.py (the
#: dimension table and `tightest()`) and src/pmus_l5_continuous_session.py
#: (`_apply_rung` intersects durable state instead of obeying it), both in the
#: image COPY set, and both honestly SIZING/safety-state. The owner reviewed
#: the change, authorized it explicitly, and required the baseline be moved
#: "truthfully ... to the resulting exact green artifact" -- which is this
#: commit, the one the 4931-test suite passed on. FORBIDDEN_CLASSES is
#: untouched and still contains SIZING; a future sizing change still fails
#: this gate and still needs its own review.
#: ADVANCED 2026-09-16 for OWNER AUTHORIZATION "fix zero-exposure market
#: concurrency accounting", on the same terms as every advance above, and this
#: one deserves the plainest statement of what moved: the changed file is
#: src/pmus_live_mandate.py -- THE WIRE GUARD ITSELF, the most safety-critical
#: file in the COPY set. Nothing in this repository is a stronger reason to
#: look before advancing.
#:
#: WHAT CHANGED, exactly: `consumption()` computes `markets_open` as "a live or
#: unresolved order, or unsettled fills" instead of "committed AND not
#: settled". The live probe filled nothing on six orders, and a market with
#: nothing in it never settles, so it held max_concurrent_markets=1 forever.
#: `markets_touched` is deliberately unchanged, the durable ledger is
#: untouched, and unresolved state still counts as OPEN.
#:
#: WHY THE BASELINE HAD TO MOVE RATHER THAN THE ALLOW-LIST GROW: this file's
#: honest class is SUBMISSION -- `consumption()` feeds `admit()`, which decides
#: whether POST /v1/orders happens at all. SUBMISSION is forbidden, so
#: allow-listing it would mean either mislabeling the change or dropping
#: SUBMISSION from FORBIDDEN_CLASSES and weakening the gate permanently for
#: every future change to the guard. The owner reviewed this change and
#: authorized it explicitly; advancing the baseline is the route that keeps
#: the gate's strength intact. FORBIDDEN_CLASSES is UNCHANGED and still
#: contains all four classes. A future change to the wire guard fails this
#: gate exactly as this one did, and still needs its own review.
#:
#: This is a REVIEW gate, not a boot gate. Advancing it grants nothing: HALT
#: still halts and the mandate is still the only authorization to spend.
#: ADVANCED 2026-09-17 for OWNER AUTHORIZATION "authenticated-book freshness
#: submission gate". The changed file is src/pmus_spring_l5_session.py, and its
#: honest class is EXECUTION -- the most forbidden class in the table below.
#: This gate decides whether a live order is sent at all. There is no way to
#: describe it as anything milder and stay truthful.
#:
#: WHAT CHANGED: a submission is now refused when the authenticated executable
#: book that validates post-only placement is stale, missing or unreadable.
#: The PRICING half is untouched -- _authenticated_trim still fails open and
#: still cannot raise a price. The forensics showed failing open was right for
#: pricing and wrong for submitting: a book discarded for being 11.2s stale was
#: replaced by a fresher, wrong gateway book and five post-only orders crossed
#: the real ask and were refused at insertion.
#:
#: WHY THE BASELINE MOVED RATHER THAN THE ALLOW-LIST GROWING: allow-listing
#: this file would mean either calling an execution change something it is not,
#: or dropping EXECUTION from FORBIDDEN_CLASSES and weakening the gate
#: permanently for every future change to the session. The owner reviewed and
#: authorized this change specifically, with seven named adversarial test
#: categories and a required counterfactual. Advancing the baseline keeps the
#: gate's strength intact. FORBIDDEN_CLASSES is UNCHANGED and still contains
#: all four classes; the next change to this file fails this gate exactly as
#: this one did.
#:
#: Still a REVIEW gate, not a boot gate. Advancing it grants nothing: HALT
#: still halts, and there is no live mandate.
#: ADVANCED 2026-09-17 for OWNER AUTHORIZATION "next CP1 live execution probe".
#: The changed file is src/pmus_l5_ramp_ladder.py and the honest class is
#: SIZING -- the ladder is the risk-envelope table.
#:
#: THIS IS THE FIRST ADVANCE THAT WIDENS. Every previous one carried a fix or a
#: tightening: the CP1 cap so min(rung, mandate) could reach an envelope, the
#: explicit sizing that replaced inherited micro defaults, B3's intersection,
#: the concurrency-accounting correction, the freshness submission gate. This
#: one deliberately RAISES a risk dimension -- markets_per_campaign 3 -> 10 --
#: and that deserves to be named rather than buried among them.
#:
#: WHAT IT DOES AND DOES NOT WIDEN. Breadth only. Per-market cap $5.00,
#: residual 3 units, concurrency 1, 6 actions per session, sizing $0.10/$0.125,
#: k, the -$6.00 daily stop and max_total_spend $15.00 are all unchanged. The
#: owner held campaign dollars flat while widening market count, so the
#: mandate's $15.00 is now strictly tighter than the rung's own cap x markets
#: worst case of $50.00 and is the binding campaign-spend ceiling at the wire.
#: More markets buys more observations, not more money.
#:
#: WHY THE RUNG WAS EDITED IN PLACE: durable state stores the rung ID, so a new
#: id would have met the stored "CP1" in B3's tightest() and been clamped back
#: to 3 markets. B3 is untouched and still intersects every dimension; it is
#: simply not handed a stale value to intersect against.
#:
#: FORBIDDEN_CLASSES is UNCHANGED and still contains all four classes. The next
#: ladder change fails this gate exactly as this one did. Still a REVIEW gate,
#: not a boot gate.
#:
#: ADVANCED 2026-09-17 for OWNER AUTHORIZATION "LIVE SHORT-HORIZON SPORTS", on
#: exactly the same terms as every advance above. The owner reviewed the change
#: through commit 2366aa3b and authorized the EXECUTION-class delta in
#: src/pmus_event_progress.py and src/pmus_l5_continuous_session.py by name.
#:
#: WHAT MOVED, and why its honest class is EXECUTION: the cohort eligibility
#: gate in pmus_l5_continuous_session generalises from a named league to
#: COHORT_ANY_LIVE='*'. AST-stripped, the functional delta is 18 lines whose
#: only semantic change is wrapping the league equality in
#: `if cohort != COHORT_ANY_LIVE:` -- not_in_live_feed, event_ended and
#: unknown_progress are byte-identical. pmus_event_progress gains one field,
#: `league`, read from the venue's primaryTag on a request already being made.
#: Both files sit in the image COPY set and the gate decides which markets
#: execution may look at, so EXECUTION is the truthful class. The alternatives
#: were to mislabel it or to drop EXECUTION from FORBIDDEN_CLASSES; the owner
#: reviewed the artifact and advanced the baseline instead.
#:
#: THIS IS NOT A WEAKENING. FORBIDDEN_CLASSES is UNCHANGED and still contains
#: all four classes, EXECUTION among them. The next execution change fails this
#: gate exactly as this one did and needs its own review. What moved is only
#: WHICH artifact the COPY set is compared against. The gate can only REFUSE
#: candidates: it computes no price, size, side or timing, and tests assert its
#: executed identifiers reach no trading parameter and no endDate.
#: ---------------------------------------------------------------- A3, 2026-09-28
#: WHY THE BASELINE ADVANCES AGAIN: the BTC15m PROFIT_LOCK_5 capacity probe pins
#: `src/pmus_micro_executor.py` as part of its artifact, so the drift in that file
#: could not be waived as irrelevant to the probe. Three items were outstanding
#: against 03f881ca -- two undisclosed deltas and one file absent from REVIEWED.
#:
#: WHAT WAS REVIEWED, and it is EVIDENCE rather than assertion. Each delta was
#: proven before it was classified, by reconstructing the 03f881ca implementation
#: verbatim and differencing it:
#:
#:   src/pmus_micro_executor.py -- `_body` delegates to `src/l5_order_body.build`
#:   instead of holding a second copy. `tests/test_r1_body_delegation_equivalence.py`
#:   compares every field across 9 prices x 8 quantities x 2 sides x post_only and
#:   finds the bodies IDENTICAL, and separately pins the NO-side 1-X wire mapping,
#:   the MICRO_NONPOSITIVE_QTY halt code and the float quantity. De-duplication with
#:   no behavioural delta, which is why it is DEDUPLICATION_PROVEN_EQUIVALENT and
#:   not SUBMISSION: there is no submission behaviour to change.
#:
#:   src/pmus_q1_executor.py -- (a) the fee coefficients now read the one
#:   authoritative schedule; PMUS republished the taker coefficient at 0.0695
#:   effective 2026-09-17 and the local 0.06 never moved, understating the cost by
#:   15.8%, which LOOSENED every fee-safe bound derived from it. (b)
#:   `complement_bound` gained a `tick` parameter because the grid is a property of
#:   the market, not of the module. `tests/test_r1_q1_delta_conservatism.py` proves
#:   omitting `tick` reproduces the 03f881ca bound exactly at every price, a coarser
#:   grid is never looser, a degenerate grid refuses instead of defaulting, and
#:   across a 9x9 grid there is NO (first_cost, ask) pair that taker_completion_ok
#:   admits now and would have refused under the stale coefficient. Both deltas move
#:   bounds only in the restricting direction, hence RISK_BOUND_TIGHTENING.
#:
#:   src/pmus_fees.py -- was ABSENT from REVIEWED and is required, because the
#:   executor now imports the schedule from it rather than carrying a copy. It is an
#:   effective-dated data module with provenance, so pre-2026-09-17 analyses stay
#:   correct. NEW_DEPENDENCY.
#:
#: WHAT WAS NOT BLESSED. Nothing else. The probe's own wiring -- the frozen
#: PROFIT_LOCK_5 decision, the taker-completion primitive and the mandate guard --
#: is NOT in this baseline; it does not exist yet and will need its own review.
#:
#: THIS IS NOT A WEAKENING. FORBIDDEN_CLASSES is UNCHANGED and still contains
#: EXECUTION, DECISION, SIZING and SUBMISSION. The cheap way through would have been
#: to label the order-body change SUBMISSION and drop SUBMISSION from the gate, or to
#: relabel it and skip the proof. Advancing the baseline on evidence keeps the gate's
#: strength intact for every future delta.
#:
#: BASELINE ADVANCED TO 8039c416 (LOCK5 CAPACITY-PROBE ARTIFACT, 2026-09-28).
#: The predecessor comment above says the probe's own wiring "does not exist yet
#: and will need its own review." It now exists and has had that review:
#: analysis/pmus/PROBE_ARTIFACT_REVIEW_8039c416.md. Five in-closure files changed,
#: each classified there against FORBIDDEN_CLASSES, with blob SHAs pinned and two
#: structural proofs recorded: the entrypoint actually composes the reviewed
#: translation (closure + negative controls), and pmus_l5_ramp_ladder is absent
#: from the 43-file reachable capability graph, so the mandate/GuardedTransport
#: boundary is the artifact's only risk/sizing authority.
#:
#: FORBIDDEN_CLASSES remains UNCHANGED. The EXECUTION-class deltas were admitted on
#: evidence, not by relabelling them or by removing a class from the gate.
#:
#: ADVANCED AGAIN TO e48960f7 (2026-09-28): the paired shadow-vs-live record.
#: One new in-closure file, `src/pmus_paired_log.py`, plus its composition on the
#: entrypoint (`pair_intent`, and `build_lock5` now building the log). Class:
#: RECORDING, not forbidden -- it decides nothing, sizes nothing and submits
#: nothing; it observes. It is nevertheless in-closure and therefore reviewed,
#: because the first live window will be written up from it: a record that could
#: default a row's live side to the shadow side would report a parity that never
#: happened. Closure is 44 files; the Dockerfile COPY list was regenerated from it
#: and is byte-identical.
REVIEWED = "e48960f7"


#: The superseded baseline, kept so the provenance is legible HERE and not only
#: in git history. Every delta it covered is preserved verbatim in PRIOR_DELTAS
#: below; those files are now PART of REVIEWED and no longer differ. The
#: mechanism's own self-tests run against this baseline, so advancing the
#: production one does not leave the gate untested.
#:
#: This is the ORIGINAL R1 review, deliberately NOT advanced to f3247262 when
#: B3 moved the production baseline. Keeping the oldest one is what keeps the
#: self-tests meaningful: PRIOR_DELTAS still differs from it, so the drift and
#: unpinned-delta tests still have something real to catch. Re-pointing it at
#: each new baseline would leave them asserting against an empty delta set.
PREVIOUS_REVIEWED = "03f881ca"

#: THE WHOLE CHAIN, so advancing a baseline can never erase an earlier review.
#: PREVIOUS_REVIEWED is only the immediate predecessor; this is the record.
REVIEWED_CHAIN = ("6adcc4c4", "03f881ca", "04cf5295", "8039c416",
                  "e48960f7")
DOCKERFILE = "deploy/l5_r1_probe/Dockerfile"

# Each entry: why this file may differ, the class it must fall in, and the
# EXACT blob the review covered.
#
# THE `sha` FIELD IS WHAT STOPS THIS BECOMING A RUBBER STAMP (OWNER 2026-09-16).
# A filename allow-list says "this file may differ" and then accepts ANY future
# content in it -- one reviewed delta silently licenses every later one. Pinning
# the reviewed blob means the accepted executable COPY set is identical except
# for deltas someone actually looked at: edit an allow-listed file again and
# verify() fails as CONTENT_DRIFT until the pin is refreshed with a new reason.
#
# EXECUTION / DECISION / SIZING / SUBMISSION are never acceptable classes here.
#: PROVENANCE ONLY -- the deltas reviewed against PREVIOUS_REVIEWED. They are
#: now inside REVIEWED and no longer differ from it. Kept verbatim, with
#: their reviewed blob ids, so the record of what was accepted and why does
#: not vanish when the baseline moves.
PRIOR_DELTAS: Dict[str, Dict[str, str]] = {
    "src/pmus_micro_executor.py": {
        "class": "DEDUPLICATION_PROVEN_EQUIVALENT",
        "sha": "d07cd09fd14d138ba20057d10feac78117bf189c",
        "why": ("REVIEWED 2026-09-28 (A3). `_body` delegates to "
                "src/l5_order_body.build instead of holding a second copy of "
                "the order body. PROVEN, not asserted: "
                "tests/test_r1_body_delegation_equivalence.py rebuilds the "
                "03f881ca implementation verbatim and finds every field "
                "identical across 9 prices x 8 quantities x 2 outcome sides x "
                "post_only, and separately pins the NO-side 1-X wire mapping, "
                "the MICRO_NONPOSITIVE_QTY halt code and the float quantity. "
                "No submission behaviour changes, so this is de-duplication "
                "rather than a SUBMISSION-class delta."),
    },
    "src/pmus_q1_executor.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "c818d5091cf1a64c35f06a909ada999efd4bb46e",
        "why": ("REVIEWED 2026-09-28 (A3). Two deltas, both restricting. "
                "(a) taker_fee/maker_rebate read src/pmus_fees.py instead of "
                "local coefficients: PMUS republished the taker coefficient at "
                "0.0695 effective 2026-09-17 and the local 0.06 never moved, "
                "understating the cost 15.8% and LOOSENING every fee-safe bound "
                "derived from it. (b) complement_bound gained a `tick` "
                "parameter because the grid belongs to the market, not the "
                "module; a hardcoded TICK returned prices like 0.475 that PMUS "
                "BTC15m does not accept. PROVEN by "
                "tests/test_r1_q1_delta_conservatism.py: omitting `tick` "
                "reproduces the 03f881ca bound exactly, a coarser grid is never "
                "looser (rounding stays DOWN), a degenerate grid refuses rather "
                "than defaulting, and across a 9x9 grid NO (first_cost, ask) "
                "pair is admitted now that the stale coefficient refused. The "
                "q=1/$1 canary constants are asserted untouched."),
    },
    "src/pmus_fees.py": {
        "class": "NEW_DEPENDENCY",
        "sha": "de25f9124405391bf538c67f37756b90bb54fc4f",
        "why": ("REVIEWED 2026-09-28 (A3). Was ABSENT from 03f881ca and is "
                "required, because the executor now imports the one "
                "authoritative fee schedule rather than carrying a copy. An "
                "effective-dated data module with provenance, so pre-2026-09-17 "
                "analyses remain correct. No execution logic."),
    },
    "src/pmus_l5_continuous_session.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "758ba95b16648594760e629e018e1ac281c58a92",
        "why": ("CHANGED 2026-09-16 after the R1 canary traded 11+ markets on "
                "a one-market authorization. Four deltas, all restricting: "
                "(a) arming now REQUIRES a valid unexpired mandate naming this "
                "service, and refuses with NO_MANDATE otherwise; (b) the "
                "transport is wrapped by the mandate guard, so every "
                "POST /v1/orders is admitted against the envelope at the wire "
                "rather than at a call site; (c) approved_continuation refuses "
                "any approval that does not POSTDATE the active mandate, which "
                "is what let three 2026-09-06 approvals continue a 2026-09-16 "
                "campaign, and an undated approval is refused; (d) the halt "
                "latch and the mandate's expiry are re-read on EVERY campaign "
                "pass, not once at boot, so an unattended process stops itself "
                "when its authorization runs out. No order verb, size, price, "
                "cap, band or admission threshold is loosened by any of it."),
    },
    "src/pmus_live_mandate.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "959de8e7c7f5cc5990e1dd7f142b91ee7008c438",
        "why": ("ADDED 2026-09-16 after the R1 canary spent $19.3295 against a "
                "$0.05 authorization across 11+ markets. The immutable "
                "per-authorization mandate and the transport guard that "
                "enforces it. It can only REFUSE: no order verb, no transport "
                "of its own, no sizing, no pricing -- it wraps a transport and "
                "raises MandateRefusal before POST /v1/orders. Enforcement "
                "moved to the wire precisely because the old rule was an "
                "allow-list of stage names that forgot spring_complete_*."),
    },
    "analysis/l5/authorization/": {
        "class": "DOCUMENTATION",
        "sha": "e5ddef9792e0f53425e7857f6e35496a5760623d",
        "why": ("ADDED 2026-09-16. The committed HALT latch and the (currently "
                "ABSENT) live_mandate.json. Data and prose, never code: HALT's "
                "presence halts, live_mandate.json's absence is the safe "
                "state. Shipped as a directory so granting or revoking an "
                "authorization is a reviewed commit rather than a Dockerfile "
                "edit. The R1 probe ships HALTED with no mandate."),
    },
    "src/pmus_spring_l5_session.py": {
        "class": "RISK_BOUND_TIGHTENING",
        "sha": "621ffeeb80e81a744ad58f4e97efca008f3745a6",
        "why": ("TWO deltas, and the class names the more consequential one. "
                "(a) OBSERVABILITY: inside a block commented 'never let "
                "observability trade', publishes tte_s as null instead of the "
                "1e9 SPORTS_TTE_DEFAULT_S sentinel; the decision path still "
                "buckets on the sentinel. (b) RISK_BOUND_TIGHTENING (OWNER "
                "2026-09-16 P0): the complement-acceptance probe now counts "
                "against the one per-market notional envelope and is tested "
                "against it BEFORE submission (counts_against_market_cap, "
                "cap_admits), and its ledger rows changed to the canonical "
                "submitted/order_terminal/filled shapes so a partial fill "
                "reconciles and ledger_qty_total can see the position. "
                "This GATES a submission, which is why it is not filed as "
                "OBSERVABILITY -- but it can only ever REFUSE one. It computes "
                "no price, no size and no side, contains no order verb, and "
                "leaves held_inventory_from_ledger untouched so no L5 decision "
                "input moves. Pinned by tests/test_market_cap_envelope.py."),
    },
    "src/l5_decision_observability.py": {
        "sha": "2f977868027299fe532ff05c99a561604df99079",
        "class": "OBSERVABILITY",
        "why": ("adds two reason constants and two emitted keys "
                "(tte_s_reason, book_implied_pair_cost_reason). Output only -- "
                "this module has no decision path."),
    },
    "src/pmus_incentive_audit.py": {
        "sha": "74354635ef6f97c36748770412a567adeccaebe4",
        "class": "ACCOUNTING_REPORTING",
        "why": ("rows_detail truncation cap raised 50 -> 2000 and any residual "
                "truncation is now REPORTED. A payout that cannot be attributed "
                "to its markets is the same failure as a fabricated zero. No "
                "fee or rebate arithmetic changes."),
    },
    "analysis/l5/venue_maintenance_windows.json": {
        "sha": "f7204c60fe5d7890380c2cb43a81394686cf95d4",
        "class": "DOCUMENTATION",
        "why": ("descriptive fields added to one incident record (resolution, "
                "orders_count_correction 10->14, withdrawn_evidence, "
                "observed_recovery_*, rejection_signature_recurred_after_recovery). "
                "The loader reads ONLY windows[].start_utc / end_utc / "
                "revisions[].end_utc_revised / revised_by / id / venue, and the "
                "diff touches none of them, so gating behaviour is unchanged."),
    },
}

#: NOTHING in the image COPY set may differ from REVIEWED except what is
#: disclosed here. The authorization directory is the one legitimate mover:
#: granting or revoking an authorization IS a change to it, by design, and the
#: pin is re-taken each time so it cannot become a rubber stamp.
ALLOWED: Dict[str, Dict[str, str]] = {
    "analysis/l5/authorization/": {
        "class": "DOCUMENTATION",
        "sha": "c2551101d730257a2dfa45fab5c449a897c4c673",
        "why": ("RE-PINNED 2026-09-18 because HALT WAS RESTORED. The delta is "
                "the addition of analysis/l5/authorization/HALT and nothing "
                "else: prose, not code, and it moves the tree in the "
                "FAIL-CLOSED direction, which is the only direction this "
                "directory may move without a fresh owner authorization. "
                "The mandate OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT "
                "EXPIRED at 2026-09-18T00:41:20Z, and HALT had not been put "
                "back, so the committed state read 'un-halted, no valid "
                "authorization' for 11.3 hours -- caught by "
                "test_the_halt_and_the_authorization_are_consistent, which is "
                "exactly what that invariant is for. Nothing traded in that "
                "window and nothing could have: l5-r1-probe ended itself at "
                "2026-09-17T13:36:57Z with CAMPAIGN_STOP:HALT, ~11 hours "
                "before the expiry, and an expired mandate is NO_MANDATE at "
                "the wire. The expired live_mandate.json is KEPT beside HALT "
                "as the spent authorization of record; deleting evidence to "
                "make a check pass is the wrong direction. This pin therefore "
                "covers a HALTED tree with no authorization in force. "
                "PREVIOUSLY pinned 35e9ace92e16856b95dd72ce477aede6604035bd "
                "for the SETTLEMENT-PENDING CONCURRENCY "
                "REPAIR (OWNER P0 'FIX SETTLED-PENDING FULLY-MATCHED "
                "CONCURRENCY DEADLOCK'), adding "
                "ACTIVATION_6_SETTLEMENT_PENDING_2026-09-17.md -- the record, "
                "prose not code. This tree carries the FRESH immutable mandate "
                "OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT, whose "
                "envelope is IDENTICAL FIELD FOR FIELD to the cohort it "
                "follows: 1 concurrent, 10 total, $5.00 per market, $15.00 "
                "total, $3.00 residual, -$6.00 daily stop, 12h. A new "
                "authorization_id, not a reuse or an enlargement, so "
                "consumption() starts at zero markets touched. A "
                "concurrency-ACCOUNTING repair is not a reason to move money, "
                "and the repair itself explicitly refuses to: a market that "
                "releases its acquisition slot still consumes one of "
                "max_total_markets, and a test pins that. HALT is ABSENT, so "
                "this pin covers an ARMED authorization -- the strongest "
                "reason the directory is gated at all. Removing HALT does not "
                "authorize trading: orders additionally require the mandate, "
                "and the halt/authorization consistency test enforces that the "
                "absence of HALT is never on its own an authorization. "
                "PREVIOUSLY, for the STOCHASTIC SELECTOR "
                "ACTIVATION (OWNER 'RESTORE FIDELITY ACTION SELECTION'), "
                "adding ACTIVATION_5_STOCHASTIC_SELECTOR_2026-09-17.md -- the "
                "record, prose not code. This tree carries the FRESH "
                "immutable mandate "
                "OWNER-2026-09-17-CP1-STOCHASTIC-SELECTOR-COHORT (1 "
                "concurrent / 10 total, $5.00 per market, $15.00 total, "
                "$3.00 residual, -$6.00 daily stop, expiry 23:45:17Z) "
                "replacing the LIVE-SHORT-HORIZON mandate whose campaign "
                "ended CAMPAIGN_STOP:HALT at 11:35:02Z. A new "
                "authorization_id, not a reuse or an enlargement, so "
                "consumption() starts at zero markets touched. THE MONETARY "
                "ENVELOPE IS IDENTICAL to the one it replaces, field for "
                "field: what was reviewed is WHICH relation the frozen "
                "model's own conditional masses select, and a behavioral "
                "correction is not a reason to move money. THE SELECTOR "
                "ITSELF IS NOT IN THIS DIRECTORY AND NOT IN THE COPY SET: "
                "it is one ENV line in the image, "
                "PMUS_SPRING_ACTION_SELECTION=stochastic, pinned there "
                "rather than as a service variable because "
                "os.getenv(ACTION_SELECTION_ENV, 'modal') makes an UNSET "
                "variable the defect rather than a neutral state. No src/ "
                "file moved at all, which is why this run shows exactly one "
                "differing path -- this one, the directory that grants "
                "spend. HALT is ABSENT, so this pin again covers an ARMED "
                "authorization. Removing HALT does not authorize trading: "
                "orders additionally require the mandate, and the "
                "halt/authorization consistency test enforces that the "
                "absence of HALT is never on its own an authorization. The "
                "tree also adds "
                "analysis/l5/live_probe/SAFETY_RESIDUAL_CAP_OVERSHOOT_2026-09-17.md "
                "OUTSIDE this directory -- a filed, deliberately UNREPAIRED "
                "safety finding, kept separate from this deployment at the "
                "owner's instruction. "
                "PREVIOUSLY, for the LIVE SHORT-HORIZON SPORTS "
                "ACTIVATION, adding ACTIVATION_4_LIVE_SHORT_HORIZON_2026-09-17.md "
                "-- the record, prose not code. "
                "cohort. This tree carries the FRESH immutable mandate "
                "OWNER-2026-09-17-CP1-LIVE-SHORT-HORIZON-COHORT (1 "
                "concurrent / 10 total, $5.00 per market, $15.00 total, "
                "$3.00 residual, -$6.00 daily stop, 12h) replacing the "
                "breadth-10 mandate whose campaign ended CAMPAIGN_STOP:HALT "
                "at 02:28:12Z. A new authorization_id, not a reuse or an "
                "enlargement, so consumption() starts at zero markets "
                "touched. NO monetary envelope moved despite the candidate "
                "universe growing from one league to every live sport: a "
                "wider choice under an unchanged cap is more CHOICE, not "
                "more money. HALT is ABSENT, so this pin covers an ARMED "
                "authorization -- the strongest reason the directory is "
                "gated at all. Removing HALT does not authorize trading: "
                "orders additionally require the mandate, and the "
                "halt/authorization consistency test enforces that the "
                "absence of HALT is never on its own an authorization. The "
                "tree also adds LIVE_SHORT_HORIZON_COHORT_2026-09-17.md and "
                "marks MLB_COHORT_PREACTIVATION_2026-09-17.md SUPERSEDED -- "
                "prose, not code. "
                "PREVIOUSLY: pinned to ARM the 10-market execution probe. "
                "HALT is REMOVED from this tree and the FRESH mandate "
                "OWNER-2026-09-17-CP1-BREADTH10-EXECUTION-PROBE remains, so "
                "this pin covers an ARMED authorization -- the strongest "
                "reason the directory is gated at all. All eight of the "
                "owner's pre-arming checks passed first, including that the "
                "execution path is BYTE-IDENTICAL to the accepted "
                "freshness-gate artifact 0edd1eb061f9. Removing HALT does "
                "not authorize trading: orders additionally require the "
                "mandate, and the halt/authorization consistency test "
                "enforces that the absence of HALT is never on its own an "
                "authorization. "
                "WHY THIS ENTRY IS ALWAYS EXERCISED: it is a "
                "DIRECTORY, and verify() compares `git show <rev>:<path>`, "
                "whose first line echoes the revision string itself. So it "
                "reads as differing even when its tree sha is identical, "
                "and the pin is therefore checked on every single run. That "
                "is the right outcome for the directory that grants spend, "
                "but it is an accident of how git prints a tree rather than "
                "a designed property, and it is recorded here so nobody "
                "later 'fixes' it without knowing what it was doing. "
                "The committed HALT latch, the live mandate when one exists, "
                "This tree carries the FRESH immutable mandate "
                "OWNER-2026-09-17-CP1-BREADTH10-EXECUTION-PROBE (1 "
                "concurrent / 10 total, $5.00 per market, $15.00 total, "
                "$3.00 residual, -$6.00 daily stop, 24h) replacing the "
                "EXHAUSTED three-market one -- a new authorization_id, not a "
                "reuse or an enlargement, so consumption() starts at zero "
                "markets touched. HALT is STILL PRESENT in this tree; "
                "arming is a separate reviewed commit. "
                "WHY THIS ENTRY IS ALWAYS EXERCISED: it is a "
                "DIRECTORY, and verify() compares `git show <rev>:<path>`, "
                "whose first line echoes the revision string itself. So it "
                "reads as differing even when its tree sha is identical, "
                "and the pin is therefore checked on every single run. That "
                "is the right outcome for the directory that grants spend, "
                "but it is an accident of how git prints a tree rather than "
                "a designed property, and it is recorded here so nobody "
                "later 'fixes' it without knowing what it was doing. "
                "The committed HALT latch, the live mandate when one exists, "
                "record. This tree adds "
                "FRESHNESS_SUBMISSION_GATE_2026-09-17.md -- prose, not code "
                "-- and HALT is STILL PRESENT with the exhausted "
                "live_mandate.json beside it. No authorization was created "
                "and nothing was armed: the gate was deployed HALTED as the "
                "owner required. Previously re-pinned after the CP1 probe "
                "COMPLETED its authorization (3 of 3 markets, 13 orders, "
                "zero fills, $0.00 moved), and before that for the "
                "concurrency-accounting fix and the second activation. The "
                "pin has now covered this directory halted, armed, halted, "
                "armed and halted again; each transition is a reviewed "
                "commit, which is the mechanism working rather than being "
                "noisy. "
                "WHY THIS ENTRY IS ALWAYS EXERCISED: it is a "
                "DIRECTORY, and verify() compares `git show <rev>:<path>`, "
                "whose first line echoes the revision string itself. So it "
                "reads as differing even when its tree sha is identical, "
                "and the pin is therefore checked on every single run. That "
                "is the right outcome for the directory that grants spend, "
                "but it is an accident of how git prints a tree rather than "
                "a designed property, and it is recorded here so nobody "
                "later 'fixes' it without knowing what it was doing. "
                "The committed HALT latch, the live mandate when one exists, "
                "authorization. HALT is back and live_mandate.json remains, "
                "now EXHAUSTED rather than invalid: the campaign touched 3 "
                "of 3 markets, submitted 13 maker orders, filled NOTHING and "
                "moved $0.00. The halt is not a defect response -- it stops "
                "a redeploy or restart from re-quoting on an already-touched "
                "market, because a bounded money-moving session must not "
                "automatically repeat. Re-arming needs a NEW authorization; "
                "the three-market allowance is spent. Record: "
                "PROBE_COMPLETE_2026-09-16.md. Previously pinned for the "
                "SECOND CP1 ACTIVATION (HALT removed, mandate present), and "
                "before that for the concurrency-accounting fix. The pin has "
                "now covered this directory halted, armed, halted, armed and "
                "halted again in one day; each transition is a reviewed "
                "commit, which is the mechanism working rather than being "
                "noisy. "
                "WHY THIS ENTRY IS ALWAYS EXERCISED: it is a "
                "DIRECTORY, and verify() compares `git show <rev>:<path>`, "
                "whose first line echoes the revision string itself. So it "
                "reads as differing even when its tree sha is identical, "
                "and the pin is therefore checked on every single run. That "
                "is the right outcome for the directory that grants spend, "
                "but it is an accident of how git prints a tree rather than "
                "a designed property, and it is recorded here so nobody "
                "later 'fixes' it without knowing what it was doing. "
                "The committed HALT latch, the live mandate when one exists, "
                "is REMOVED from this tree and live_mandate.json remains, "
                "so this pin covers an ARMED authorization -- the strongest "
                "reason the directory is gated at all. The pin has now "
                "covered it halted, armed, halted, and armed again in one "
                "day; each transition is a reviewed commit, which is the "
                "mechanism working rather than being noisy. Removing HALT "
                "does not authorize trading: orders additionally require "
                "the mandate, and the halt/authorization consistency test "
                "enforces that the absence of HALT is never on its own an "
                "authorization. Previously pinned for the "
                "concurrency-accounting fix, which added "
                "CONCURRENCY_ACCOUNTING_2026-09-16.md and "
                "FINDING_REFUSAL_CLASSIFICATION_2026-09-16.md. "
                "WHY THIS ENTRY IS ALWAYS EXERCISED: it is a "
                "DIRECTORY, and verify() compares `git show <rev>:<path>`, "
                "whose first line echoes the revision string itself. So it "
                "reads as differing even when its tree sha is identical, "
                "and the pin is therefore checked on every single run. That "
                "is the right outcome for the directory that grants spend, "
                "but it is an accident of how git prints a tree rather than "
                "a designed property, and it is recorded here so nobody "
                "later 'fixes' it without knowing what it was doing. "
                "The committed HALT latch, the live mandate when one exists, "
                "tree adds CONCURRENCY_ACCOUNTING_2026-09-16.md and "
                "FINDING_REFUSAL_CLASSIFICATION_2026-09-16.md -- both "
                "records -- and HALT is still present with "
                "live_mandate.json beside it: the HALTED state the owner "
                "required be deployed and verified BEFORE any "
                "re-activation. WHY THIS ENTRY IS ALWAYS EXERCISED: it is a "
                "DIRECTORY, and verify() compares `git show <rev>:<path>`, "
                "whose first line echoes the revision string itself. So it "
                "reads as differing even when its tree sha is identical, "
                "and the pin is therefore checked on every single run. That "
                "is the right outcome for the directory that grants spend, "
                "but it is an accident of how git prints a tree rather than "
                "a designed property, and it is recorded here so nobody "
                "later 'fixes' it without knowing what it was doing. "
                "The committed HALT latch, the live mandate when one exists, "
                "and the arming records. Data and prose, never code: HALT's "
                "presence halts and live_mandate.json's absence is the safe "
                "state. Shipped as a directory so granting or revoking an "
                "authorization is a reviewed commit rather than a Dockerfile "
                "edit. RE-PINNED 2026-09-16 after the first live activation STOPPED itself and HALT was re-committed. The pin has now covered the directory halted, armed, and halted again in one day, which is exactly the point: each transition is a reviewed commit. Previously pinned for the ACTIVATION, when the HALT latch was REMOVED from this tree and live_mandate.json remains. This pin now covers an ARMED authorization, which is the strongest reason the directory is gated at all -- both granting spend and lifting the halt are reviewed commits. Re-committing HALT is likewise a commit, and stops the probe on the next boot. Previously pinned for live_mandate.json ITSELF -- the first time this pin covers an actual AUTHORIZATION rather than a record of one. That is the directory doing its job: granting the authorization is a reviewed commit, and the gate makes it one. Earlier pins today covered ARMING_BLOCKED_3_..., B3_PERSISTED_RUNG_INTERSECTION_... and CP1_EXPLICIT_SIZING_..., after the B3 record's "
                "steps 6-7 production verification. The chain: "
                "ARMING_BLOCKED_3_... reported that durable state would "
                "re-apply R3L over the pinned CP1; "
                "B3_PERSISTED_RUNG_INTERSECTION_... records the authorized "
                "fix and now its verified-in-production result. The gate "
                "caught every one of those commits, which is the pin working "
                "rather than the pin being noisy: a prose record still has to "
                "be looked at before it rides into the image. HALT is still "
                "present and live_mandate.json is still absent in this tree."),
    },
}


FORBIDDEN_CLASSES = ("EXECUTION", "DECISION", "SIZING", "SUBMISSION")


def copy_sources(dockerfile: str = DOCKERFILE) -> List[str]:
    out = []
    for line in open(dockerfile):
        if line.startswith("COPY "):
            out.append(line.split()[1])
    return sorted(set(out))


def _blob(rev: str, path: str):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _blob_sha(rev: str, path: str) -> str:
    """Git's own object id for the file at that revision. Cheap, stable, and
    it is the same identity git uses, so nothing here has to hash bytes."""
    r = subprocess.run(["git", "rev-parse", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def pin_shas(head: str = "HEAD") -> Dict[str, str]:
    """The blob ids to paste into ALLOWED[...]['sha'] after a review.

    Deliberately NOT called by verify(): a pin that refreshes itself is not a
    pin. Refreshing one is a human act, and the reason string is expected to
    change with it."""
    return {f: _blob_sha(head, f) for f in ALLOWED}


def verify(head: str = "HEAD", reviewed: str = REVIEWED) -> Dict[str, Any]:
    """Recompute the delta over the image's COPY set and classify every file."""
    differing, absent, identical = [], [], []
    for src in copy_sources():
        a, b = _blob(reviewed, src), _blob(head, src)
        if a is None:
            absent.append(src)
        elif a == b:
            identical.append(src)
        else:
            differing.append(src)
    # A FILE ADDED TO THE COPY SET IS A DELTA TOO. It is absent from the
    # reviewed artifact, so it cannot be compared -- which makes disclosure
    # MORE important, not less. An addition is accepted on exactly the same
    # terms as a change: named in ALLOWED, given a non-execution class, and
    # pinned to the blob that was reviewed. Anything else still fails.
    added = [f for f in absent if f in ALLOWED]
    absent = [f for f in absent if f not in ALLOWED]
    undisclosed = [f for f in differing if f not in ALLOWED]
    bad_class = [f for f in differing + added
                 if f in ALLOWED and ALLOWED[f]["class"] in FORBIDDEN_CLASSES]
    # CONTENT, not filename. An allow-listed file whose blob is not the one the
    # review covered is drift, and drift fails exactly like an undisclosed
    # delta -- otherwise one accepted change licenses every later change to
    # the same file, which is the rubber stamp this is here to prevent.
    drift = []
    for f in differing + added:
        pinned = str((ALLOWED.get(f) or {}).get("sha") or "")
        actual = _blob_sha(head, f)
        if not pinned or not actual or pinned != actual:
            drift.append({"file": f, "pinned": pinned or None,
                          "actual": actual or None,
                          "reason": ("no reviewed blob pinned" if not pinned
                                     else "content differs from the reviewed "
                                          "blob")})
    ok = not undisclosed and not bad_class and not absent and not drift
    return {
        "label": LABEL, "reviewed_artifact": reviewed, "head": head,
        "copy_sources": len(identical) + len(differing) + len(absent),
        "identical": len(identical),
        "differing": differing,
        "absent_from_reviewed": absent,
        "added_and_disclosed": added,
        "undisclosed": undisclosed,
        "forbidden_class": bad_class,
        "content_drift": drift,
        "deltas": {f: ALLOWED[f] for f in differing + added if f in ALLOWED},
        "ACCEPTANCE": ok,
        "why": ("every differing file is on the allow-list with a "
                "non-execution class AND its blob is the exact one reviewed; "
                "every COPYed file absent from the reviewed artifact is a "
                "disclosed, classified, pinned ADDITION" if ok
                else "an undisclosed, execution-class or drifted delta is "
                     "present"),
    }
