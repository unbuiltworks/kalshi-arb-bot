# Dedicated PMUS SPRING-L5 bounded-probe image.
#
# DISARMED BY DEFAULT. The probe entry guard exits before credentials,
# transport construction, or any network activity unless its isolated
# PMUS_SPRING_* dual key, fresh AUTHENTICATED OPEN evidence (exact slug,
# two-sided served book, q1 touch depth, observation cadence, venue
# market-open safety, in-distribution canonical L5 OPEN recommendation --
# NOT completion history, per OWNER 5551236878), and
# durable state mount all pass. This image is intentionally separate from q1,
# recorder, collector, and Gemini services.
#
# Probe-1 strategy invariants remain in src/pmus_spring_l5_session.py:
# one market, maker-only, one outstanding order, account-wide flat/open-order
# preflight, exact Decimal partials, <=$0.05 cumulative OPEN/EXTEND notional,
# <=0.98 spring observation cap with the >=1c-after-known-fees hard bound
# always in the complement min() (OWNER DECISION 5533476555), cancel/deadman,
# settlement-only realized P&L. No taker path exists in spring mode.
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
COPY src/pmus_r5ft_ledger_repair.py src/pmus_r5ft_ledger_repair.py
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
COPY deploy/pmus_spring_l5/apply_complete_reject_retry.py deploy/pmus_spring_l5/apply_complete_reject_retry.py
RUN python3 deploy/pmus_spring_l5/apply_complete_reject_retry.py \
    && python3 -m py_compile src/pmus_spring_l5_session.py

COPY src/pmus_micro_executor.py src/pmus_micro_executor.py
COPY src/pmus_fill_ledger.py src/pmus_fill_ledger.py
COPY src/pmus_q1_executor_v4.py src/pmus_q1_executor_v4.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
# OWNER 5553674206: SHADOW fields (seeded draw, L5 target size and
# aggressiveness) from the offline fidelity pass; read-only lookups
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
# OWNER 5554127702 #4: prebuilt evidence-driven ramp ladder (inert: R1
# unless PMUS_SPRING_RUNG is set by the owner; worst cases logged at boot)
COPY src/pmus_l5_ramp_ladder.py src/pmus_l5_ramp_ladder.py
# import closure only: pmus_l5_continuous_session imports the mandate guard.
# Adding the file does NOT deploy this service -- it stays HALTED.
COPY src/pmus_live_mandate.py src/pmus_live_mandate.py
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
RUN touch src/__init__.py

# PMUS_SPRING_RUNG_MARKETS=1 is the CAMPAIGN'S MARKET ALLOWANCE and it lives
# here deliberately, after a failed deploy taught the lesson.
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
ENV PMUS_SERVICE_ROLE=spring-l5-probe \
    PMUS_Q1_STATE_DIR=/state/pmus-spring \
    PMUS_SPRING_MAX_ACTIONS=6 \
    PMUS_SPRING_RUNG_MARKETS=1 \
    PYTHONUNBUFFERED=1

# Deployment guard delegates to the canonical session after safety checks.
# The repair touches local ledger state only, verifies exact convergence, and
# becomes a no-op after success; only then may the live session boot.
# Previous direct form retained here for structural provenance/tests:
# exec python3 -m src.pmus_spring_l5_session
# One-shot form retained for provenance/tests:
# exec python3 -m src.pmus_spring_l5_probe_entry
CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = spring-l5-probe || { echo SERVICE_ROLE_MISMATCH; exit 64; }; python3 -m src.pmus_r5ft_ledger_repair && exec python3 -m src.pmus_l5_continuous_session"]
