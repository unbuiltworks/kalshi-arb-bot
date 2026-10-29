# KALSHI 15M CRYPTO L5 SHADOW -- read-only, keyless, 24/7
# (issue #167 comment 5584095017 workstream B).
#
# The readiness survey found src/kalshi_l5_shadow.py was a one-shot tape
# ANALYSER whose tape had no producer: src/policy_d_port/collector.py is a
# GEMINI WebSocket collector, not a Kalshi one. This image runs the missing
# producer plus the loop that feeds the existing analyser.
#
# NO CREDENTIALS. Public keyless GETs to api.elections.kalshi.com only.
# There is no order path in this image, no PMUS executor, no WS recorder
# and no shared volume. It never writes analysis/l5/**; the frozen model is
# copied in READ-ONLY so the same L5 brain (sha bbb55bf90638d2bd) answers
# here as on PMUS.
#
# This lane keeps LITERAL time-to-settlement: a Kalshi 15M window serves
# its own open_time/close_time and settles deterministically, so the sports
# event-progress translation is deliberately not applied.
FROM python:3.11-slim

WORKDIR /app
# pure stdlib: no pip install

COPY src/kalshi_l5_collector.py src/kalshi_l5_collector.py
COPY src/kalshi_l5_shadow.py src/kalshi_l5_shadow.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
# frozen artifact, READ-ONLY
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
RUN touch src/__init__.py

ENV KALSHI_L5_SERVICE_ROLE=kalshi-l5-shadow \
    KALSHI_L5_STATE_DIR=/data/kalshi_l5 \
    PYTHONUNBUFFERED=1

# boot tripwire (deploy/RAILWAY_ISOLATION.md pattern): a cross-built image
# crashes visibly instead of running the wrong worker
CMD ["sh", "-c", "test \"$KALSHI_L5_SERVICE_ROLE\" = kalshi-l5-shadow || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.kalshi_l5_collector"]
