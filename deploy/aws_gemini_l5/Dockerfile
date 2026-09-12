# Gemini L5 READ-ONLY shadow for AWS.
# Public Gemini endpoints only. No Gemini credentials, no order transport,
# and no live execution path are present in this image.
FROM python:3.11-slim
WORKDIR /app

COPY src/gemini_l5_shadow_service.py src/gemini_l5_shadow_service.py
COPY src/gemini_l5_shadow_v2.py src/gemini_l5_shadow_v2.py
COPY src/gemini_l5_queue_shadow.py src/gemini_l5_queue_shadow.py
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
# The service's fail-closed role guard accepts only gemini-l5-shadow.
# AWS remains distinguished by container/image/state path, not by weakening the guard.
ENV GEMINI_SHADOW_ROLE=gemini-l5-shadow \
    GEMINI_SHADOW_STATE_DIR=/state/gemini-shadow \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.gemini_l5_shadow_service"]
