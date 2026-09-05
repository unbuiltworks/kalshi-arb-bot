# Gemini L5 READ-ONLY shadow (OWNER 5554553793 B). Public endpoints only:
# no credentials, no order path, no PMUS module, no shared volume. Replays
# each expired BTC-5m window through the canonical v2 policy under every
# execution shell (rest/cancel discipline) with strict fills. Nothing is
# armed; nothing can be armed from this image.
FROM python:3.11-slim
WORKDIR /app
COPY src/gemini_l5_shadow_service.py src/gemini_l5_shadow_service.py
COPY src/gemini_l5_shadow_v2.py src/gemini_l5_shadow_v2.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
RUN touch src/__init__.py
ENV GEMINI_SHADOW_ROLE=gemini-l5-shadow \
    GEMINI_SHADOW_STATE_DIR=/state/gemini-shadow \
    PYTHONUNBUFFERED=1
CMD ["python3", "-m", "src.gemini_l5_shadow_service"]
