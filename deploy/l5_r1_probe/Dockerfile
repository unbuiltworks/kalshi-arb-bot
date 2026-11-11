# LANE_A_EXECUTION_MEASUREMENT_PROBE_V0 -- isolated R1 probe image.
#
# Derived from deploy/pmus_spring_l5/Dockerfile by removing every source
# mutation and every legacy coupling. The legacy image is NOT reusable for this
# probe and the reasons are structural, not stylistic:
#
#   1. its Railway startCommand runs `sed -i` at boot to rewrite REARM_RUNG
#      from "R5F" to "R5FT" -- the running code is not the reviewed source;
#   2. it applies a BUILD-TIME source transform
#      (apply_complete_reject_retry.py) to pmus_spring_l5_session.py;
#   3. it boots src.pmus_r5ft_ledger_repair, an R5FT-specific reconciliation
#      targeted at named legacy markets and legacy campaign state;
#   4. it pins PMUS_Q1_STATE_DIR=/state/pmus-spring, the legacy campaign state.
#
# THIS IMAGE HAS NONE OF THEM. Source is copied and executed unmodified: no
# sed, no build-time patch, no generated code. A test asserts that over this
# file rather than trusting the comment.
#
# CONSEQUENCE, STATED RATHER THAN HIDDEN: without
# apply_complete_reject_retry.py this probe does NOT retry a COMPLETE that the
# venue rejects on a post-only race. That is a real behavioural difference from
# the legacy image. It is the correct trade here -- the requirement is exact
# reviewed source, and a measurement probe that adapts less is easier to
# reconcile, not harder. Both paths remain maker/post-only either way.
#
# THE RUNG IS PINNED IN THE IMAGE, not left to a service variable, because a
# service variable overrides image ENV and the legacy service demonstrates how
# that goes wrong. PMUS_SPRING_RUNG=CP1 and PMUS_SPRING_RUNG_MARKETS=10 are
# both here, in the repo, in the image, and under test.
# (OWNER 2026-09-16 Option A: was R1/1. CP1's $5.00 cap is what lets
# min(rung, mandate) reach the authorized envelope; min() is untouched.
# OWNER 2026-09-17: the allowance goes 3 -> 10 for the execution-measurement
# probe. BOTH this env AND the rung's markets_per_campaign had to move,
# because _rung_markets_of takes min(env, rung) -- widening one alone would
# have left the other silently clamping the authorization. The mandate's
# max_total_markets is the third, and all three now read 10. max_total_spend
# stays $15.00, so breadth buys observations, not dollars.)
#
# ARMING: this image sets NO PMUS_SPRING_LIVE / _ARM / _SESSION_ID. On a fresh
# service that namespace is empty, so armed() is False by construction and the
# session runs SHADOW. Arming remains an explicit owner act on an empty
# namespace rather than a mutation of an already-populated legacy one.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "cryptography>=42"

# continuous campaign (OWNER 5552036210): select -> sequential session ->
# reselect. Inherits every guard below; DISARMED it is a shadow selector.
COPY src/pmus_l5_continuous_session.py src/pmus_l5_continuous_session.py
# Residual quarantine is imported at module scope by the continuous session.
# It ships dark unless PMUS_RESIDUAL_QUARANTINE is explicitly enabled, but the
# module must still exist in the image or the probe cannot boot at all.
COPY src/pmus_residual_quarantine.py src/pmus_residual_quarantine.py
# Read-only risk-set audit (OWNER 2026-09-11): emits the risk-bearing set
# beside the scheduled one and alarms on held->absent-while-still-charged.
# Imported at module scope by the continuous session, so it must be in the
# image or the probe cannot boot -- the 565b4cde shape.
COPY src/pmus_risk_set_audit.py src/pmus_risk_set_audit.py
# The owner-approved TRANSITION stop (OWNER 2026-09-11). Imported by the
# continuous session at the boot re-arm gate, so an image without it does not
# degrade -- the probe fails to import and never boots. Same shape as the
# analysis/l5 file this image once omitted.
COPY src/pmus_transition_stop.py src/pmus_transition_stop.py
# One-shot migration close (OWNER 2026-09-11). Imported at module scope by
# pmus_l5_continuous_session, so an image without these two files does not
# degrade -- it fails to import and the probe never boots. That is the same
# shape as the analysis/l5 file this image once omitted; a test binds these
# COPY lines to the import.
COPY src/pmus_migration_close.py src/pmus_migration_close.py
COPY src/pmus_migration_close_live.py src/pmus_migration_close_live.py
# read-only settlement audit (venue POSITION_RESOLUTION activities) so the
# campaign can record the authoritative realized result of a held market
COPY src/pmus_settlement_auditor.py src/pmus_settlement_auditor.py
# One-time local accounting correction for the two venue-confirmed settled
# markets inflated by the bad venue-trade backfill. It is idempotent and
# fail-closed; after convergence it is a no-op on every restart.
# read-only batch pass over the ledger's unsettled set (owner 2026-09-11:
# "no order path, no re-arm, no new risk"). Present in the image so the pass
# can be run without a code change; it is NOT the start command.
COPY src/pmus_settlement_sweep.py src/pmus_settlement_sweep.py
# read-only incentive economics audit (OWNER 5561142758): earnings endpoint,
# activity credits, trade fee fields, balances, published programs
COPY src/pmus_incentive_audit.py src/pmus_incentive_audit.py
# the reward-status split the audit reports with (OWNER 5588925913): a
# SKIPPED or PENDING reward is an accrual, never credited income. The audit
# imports this, so it MUST be in the image -- the import is also guarded so
# a missing file degrades the audit instead of halting the lane.
COPY src/l5_realized_bridge.py src/l5_realized_bridge.py
# SHADOW-ONLY cross instrumentation, imported at module scope by the session
# (2026-09-13). Stdlib only; no transport, no credentials, no submission path
# of any kind. It is a boot-scope import, so an image without it does not
# degrade -- the probe fails to import and never boots. That is deliberate:
# the packaging test derives the required set from the imports, so this COPY
# is bound to the import rather than to anyone remembering it.
COPY src/pmus_cross_shadow_live.py src/pmus_cross_shadow_live.py
COPY src/l5_decision_observability.py src/l5_decision_observability.py
COPY src/pmus_spring_l5_probe_entry.py src/pmus_spring_l5_probe_entry.py
COPY src/pmus_spring_l5_session.py src/pmus_spring_l5_session.py

# R5FT execution fix is a strict build-time transform. Source mismatch fails
# the image build; generated session code is then compiled before startup.

COPY src/pmus_micro_executor.py src/pmus_micro_executor.py
COPY src/pmus_fill_ledger.py src/pmus_fill_ledger.py
COPY src/pmus_q1_executor_v4.py src/pmus_q1_executor_v4.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
# OWNER 5553674206: SHADOW fields (seeded draw, L5 target size and
# aggressiveness) from the offline fidelity pass; read-only lookups
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
# OWNER 5554127702 #4: prebuilt evidence-driven ramp ladder (inert: R1
# unless PMUS_SPRING_RUNG is set by the owner; worst cases logged at boot)
COPY src/pmus_l5_ramp_ladder.py src/pmus_l5_ramp_ladder.py
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
# strict-evidence composer read at admission time
# (PMUS_SPRING_EVIDENCE_STATUS_PATH) plus its closure. Read-only status
# parsing; carries no order path and no credentials of its own.
COPY src/pmus_spring_evidence_transfer.py src/pmus_spring_evidence_transfer.py
COPY src/pmus_spring_shadow_replay.py src/pmus_spring_shadow_replay.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
# venue-served GAME POSITION (period/clock/score) for the live sports slate.
# One bounded keyless GET, cached; REPORTED per candidate and fed to the
# diagnostic pair_score only -- the candidate ORDER stays L5-state fit
# (OWNER 5556232035 #3). Carries no credentials and no order path.
COPY src/pmus_event_progress.py src/pmus_event_progress.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY src/l5_fidelity_gate.py src/l5_fidelity_gate.py
COPY analysis/l5/l5_fidelity_gate.json analysis/l5/l5_fidelity_gate.json
COPY analysis/l5/venue_maintenance_windows.json analysis/l5/venue_maintenance_windows.json
COPY analysis/l5/l5_cadence_behavioral_unit.json analysis/l5/l5_cadence_behavioral_unit.json
# OWNER campaign approvals (evidence continuation): read at the advance gate
COPY analysis/l5/l5_campaign_approvals.json analysis/l5/l5_campaign_approvals.json
# THE AUTHORIZATION, AND THE HALT, TRAVEL WITH THE IMAGE.
# A directory rather than two named files, so granting or revoking an
# authorization is a reviewed commit and never a Dockerfile edit. HALT present
# = the service enters safe_shutdown before armed() is consulted.
# live_mandate.json ABSENT = no mandate = no orders, which is the safe state.
COPY analysis/l5/authorization/ analysis/l5/authorization/
COPY src/pmus_live_mandate.py src/pmus_live_mandate.py
RUN touch src/__init__.py

# PMUS_SPRING_RUNG_MARKETS is the CAMPAIGN'S MARKET ALLOWANCE and it lives
# here deliberately, after a failed deploy taught the lesson. (Its value was
# 1 until OWNER 2026-09-16 Option A authorized three markets, and 3 until
# OWNER 2026-09-17 authorized ten; the lesson below is about WHERE it lives,
# not what it is set to.)
#
# WHY IT IS NEEDED AT ALL: durable state persists the R5F campaign, whose
# markets_per_campaign is CONTINUOUS. A campaign booting without an allowance
# never reaches its advance gate, so it never consumes the owner's R5FT
# approval and instead RESUMES R5F TRADING at the 0.98 ceiling R5FT replaces.
#
# WHY NOT IN THE START COMMAND: it was, as
# "PMUS_SPRING_RUNG_MARKETS=1 python3 -m src.pmus_l5_continuous_session", and
# deployment eb3fc076 FAILED at 2026-09-11T21:22:53Z. The build was clean
# (37/37, image pushed) and the volume mounted, but there is no "Starting
# Container" line in the logs at all -- Railway did not shell-interpret the
# env prefix, so the container was never created and Python never ran. An
# env-var prefix is a SHELL feature; a start command is not guaranteed a
# shell.
#
# WHY NOT ONLY THE RAILWAY VARIABLE: it is set there too, but that write
# cannot be read back on this service (every config read returns the same
# schema error), so a silently failed write would mean the next deploy
# resumes R5F. Here it is in the repo, in the image, and under test.

# Every value the probe's envelope depends on, pinned in the image.
ENV PMUS_SERVICE_ROLE=l5-r1-probe \
    PMUS_Q1_STATE_DIR=/state/l5-r1-probe \
    # OWNER 2026-09-16 Option A: CP1 is the bounded-probe rung whose
    # $5.00 cap lets min(rung, mandate) reach the authorized envelope.
    # min() is untouched -- the mandate still caps, and CP1 cannot
    # enlarge it. Unknown ids still fall back to R1.
    PMUS_SPRING_RUNG=CP1 \
    # OWNER 2026-09-16 Option A authorized max_total_markets 3, and CP1's
    # markets_per_campaign is 3. This env override would otherwise pin the
    # campaign to ONE market and silently under-run the authorization. The
    # variable itself stays (durable R5F state must never boot without an
    # allowance); only its value is aligned to what was authorized. The
    # mandate's max_total_markets still binds at the wire regardless.
    PMUS_SPRING_RUNG_MARKETS=10 \
    PMUS_SPRING_MAX_ACTIONS=6 \
    # OWNER 2026-09-17 "LIVE SHORT-HORIZON SPORTS". The cohort restriction,
    # pinned in the IMAGE for the same reason the rung and the market
    # allowance are: a Railway service variable overrides image ENV, so an
    # envelope that depends on one is an envelope run from somewhere other
    # than where it is stated. "*" is the any-live sentinel -- it relaxes the
    # league equality and NOTHING else, so live-feed presence, a recognised
    # progress token and not-ended are all still required. Unset would mean
    # NO cohort restriction at all, which is why it is set explicitly rather
    # than left to a default.
    PMUS_L5_COHORT=* \
    # OWNER 2026-09-17 "RESTORE FIDELITY ACTION SELECTION". The executed
    # relation is the EXISTING deterministic seeded inverse-CDF draw from the
    # frozen model's own conditional masses, not their argmax. Pinned in the
    # IMAGE so a restart cannot silently fall back to the "modal" default:
    # os.getenv(ACTION_SELECTION_ENV, "modal") means an UNSET variable is the
    # defect, not a neutral state. No service variable of this name exists, so
    # the image value is authoritative.
    #
    # WHY: argmax over a stochastic policy can never select a minority
    # relation. Live, COMPLETE carried p=0.2563-0.3682 on 76 of 77 decisions
    # and modal chose it 0 times, producing one unbroken EXTEND run of 76
    # while L5 completes 46-54% from the same imb1-10 states. FIDELITY
    # correction, not a P&L optimization: the frozen behavioral gate scores
    # l1_sampled_vs_l5, i.e. the draw, and the sampler already existed.
    #
    # This changes WHICH relation is chosen and nothing else. Every safety
    # gate applies to the drawn relation unchanged, and a sampled COMPLETE
    # the shell refuses is recorded as a refusal with its reason -- it is
    # never substituted with EXTEND.
    PMUS_SPRING_ACTION_SELECTION=stochastic \
    PYTHONUNBUFFERED=1

# The role guard is this probe's own, so the legacy role cannot start this
# image and this role cannot start the legacy one. No sed, no repair step, no
# generated code -- the reviewed module is executed directly.
CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = l5-r1-probe || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.pmus_l5_continuous_session"]
