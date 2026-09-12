# Gemini L5 READ-ONLY shadow. Public endpoints only: no credentials,
# no order path, no shared execution state. Queue-aware collection retains
# 25-level depth while canonical replay remains strict/theoretical.
FROM python:3.11-slim
WORKDIR /app
COPY src/gemini_l5_shadow_service.py src/gemini_l5_shadow_service.py
COPY src/gemini_l5_shadow_v2.py src/gemini_l5_shadow_v2.py
COPY src/gemini_l5_queue_shadow.py src/gemini_l5_queue_shadow.py
COPY src/gemini_l5_queue_service.py src/gemini_l5_queue_service.py
COPY src/gemini_l5_complement_cf.py src/gemini_l5_complement_cf.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
COPY src/gemini_l5_regime_study.py src/gemini_l5_regime_study.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY src/l5_fidelity_gate.py src/l5_fidelity_gate.py
COPY analysis/l5/l5_fidelity_gate.json analysis/l5/l5_fidelity_gate.json
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
COPY analysis/l5/gemini_transfer_gate.json analysis/l5/gemini_transfer_gate.json
COPY analysis/l5/gemini_hypothesis_two_sided_flow.json analysis/l5/gemini_hypothesis_two_sided_flow.json
COPY analysis/l5/gemini_research_arm_stochastic.json analysis/l5/gemini_research_arm_stochastic.json
COPY analysis/l5/gemini_regime_candidates.json analysis/l5/gemini_regime_candidates.json
RUN touch src/__init__.py
ENV GEMINI_SHADOW_ROLE=gemini-l5-shadow \
    GEMINI_SHADOW_STATE_DIR=/state/gemini-shadow \
    PYTHONUNBUFFERED=1
CMD ["python3", "-m", "src.gemini_l5_queue_service"]
