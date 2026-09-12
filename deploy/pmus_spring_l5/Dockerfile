# Dedicated PMUS SPRING-L5 bounded-probe image.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/pmus_l5_continuous_session.py src/pmus_l5_continuous_session.py
COPY src/pmus_residual_quarantine.py src/pmus_residual_quarantine.py
COPY src/pmus_risk_set_audit.py src/pmus_risk_set_audit.py
COPY src/pmus_transition_stop.py src/pmus_transition_stop.py
COPY src/pmus_migration_close.py src/pmus_migration_close.py
COPY src/pmus_migration_close_live.py src/pmus_migration_close_live.py
COPY src/pmus_settlement_auditor.py src/pmus_settlement_auditor.py
COPY src/pmus_r5ft_ledger_repair.py src/pmus_r5ft_ledger_repair.py
COPY src/pmus_settlement_sweep.py src/pmus_settlement_sweep.py
COPY src/pmus_incentive_audit.py src/pmus_incentive_audit.py
COPY src/l5_realized_bridge.py src/l5_realized_bridge.py
COPY src/pmus_spring_l5_probe_entry.py src/pmus_spring_l5_probe_entry.py
COPY src/pmus_spring_l5_session.py src/pmus_spring_l5_session.py

# R5FT safety/execution fixes are strict build-time transforms. Either source
# mismatch fails the image build; generated session code is then compiled.
COPY deploy/pmus_spring_l5/apply_projected_residual_cap.py deploy/pmus_spring_l5/apply_projected_residual_cap.py
COPY deploy/pmus_spring_l5/apply_complete_reject_retry.py deploy/pmus_spring_l5/apply_complete_reject_retry.py
RUN python3 deploy/pmus_spring_l5/apply_projected_residual_cap.py \
    && python3 deploy/pmus_spring_l5/apply_complete_reject_retry.py \
    && python3 -m py_compile src/pmus_spring_l5_session.py

COPY src/pmus_micro_executor.py src/pmus_micro_executor.py
COPY src/pmus_fill_ledger.py src/pmus_fill_ledger.py
COPY src/pmus_q1_executor_v4.py src/pmus_q1_executor_v4.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_l5_ramp_ladder.py src/pmus_l5_ramp_ladder.py
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
COPY src/pmus_spring_evidence_transfer.py src/pmus_spring_evidence_transfer.py
COPY src/pmus_spring_shadow_replay.py src/pmus_spring_shadow_replay.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_event_progress.py src/pmus_event_progress.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY src/l5_fidelity_gate.py src/l5_fidelity_gate.py
COPY analysis/l5/l5_fidelity_gate.json analysis/l5/l5_fidelity_gate.json
COPY analysis/l5/venue_maintenance_windows.json analysis/l5/venue_maintenance_windows.json
COPY analysis/l5/l5_cadence_behavioral_unit.json analysis/l5/l5_cadence_behavioral_unit.json
COPY analysis/l5/l5_campaign_approvals.json analysis/l5/l5_campaign_approvals.json
RUN touch src/__init__.py

ENV PMUS_SERVICE_ROLE=spring-l5-probe \
    PMUS_Q1_STATE_DIR=/state/pmus-spring \
    PMUS_SPRING_MAX_ACTIONS=6 \
    PMUS_SPRING_RUNG_MARKETS=1 \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = spring-l5-probe || { echo SERVICE_ROLE_MISMATCH; exit 64; }; python3 -m src.pmus_r5ft_ledger_repair && exec python3 -m src.pmus_l5_continuous_session"]
