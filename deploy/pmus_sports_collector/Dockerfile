# Keyless READ-ONLY Polymarket US sports forward collector.
# Isolated service: public gateway.polymarket.us GETs only — no API keys,
# no authenticated endpoints, no order path. See #167.
#
# Production entrypoint uses the authoritative /v1 live-sports wrapper so
# every live event is considered before the bounded polling cap is filled.
# Persistence: /state/pmus-sports is durable only with a mounted volume.
FROM python:3.11-slim

WORKDIR /app
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_sports_live_collector.py src/pmus_sports_live_collector.py
COPY src/pmus_sports_offers_hotfix.py src/pmus_sports_offers_hotfix.py
COPY src/pmus_status_report.py src/pmus_status_report.py
# sequential L5 decision layer (issue #167 comment 5535231759): frozen
# read-only policy + its model artifact; no executor module enters this
# keyless image
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
RUN touch src/__init__.py

# Baked role tripwire: cross-built services fail closed. The live wrapper
# deliberately defaults to the full sports slate (all leagues, no prefix
# filter), while the request budget/cap remain bounded and read-only.
ENV PMUS_SERVICE_ROLE=collector \
    PMUS_COLLECTOR_ROOT=/state/pmus-sports \
    PMUS_COLLECTOR_LEAGUES=all \
    PMUS_COLLECT_INCENTIVE_MARKETS=1 \
    PMUS_POLL_SECONDS=5 \
    PMUS_DISCOVERY_SECONDS=60 \
    PMUS_RATE_BUDGET_RPS=12 \
    PMUS_MAX_MARKETS=64 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.pmus_sports_live_collector"]
