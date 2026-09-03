# Authenticated READ-ONLY Polymarket US markets-stream recorder.
# Subscribes to wss://api.polymarket.us/v1/ws/markets (book + trade tape)
# and appends verbatim JSONL under /state/pmus-ws. The only outbound
# frames are subscribe/unsubscribe/heartbeat (guard-enforced + tested);
# no order endpoint or private stream exists in this worker.
#
# DEPLOYMENT ISOLATION (issue #167 comment 5521210994): Railway's legacy
# railway.json Config-as-Code is deprecated. The supported permanent pin
# is the per-service variable
#     RAILWAY_DOCKERFILE_PATH=/deploy/pmus_ws_recorder/Dockerfile
# (collector service pins its own Dockerfile the same way). As a
# repo-side tripwire this image BAKES its role below: if a branch push
# ever cross-builds services again, the wrong worker exits loudly with
# SERVICE_ROLE_MISMATCH at startup instead of running silently.
# See deploy/RAILWAY_ISOLATION.md for the acceptance checklist.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_priority_queue.py src/pmus_priority_queue.py
# L5 action-shadow emission (per-market l5_shadow on every status row)
COPY src/pmus_l5_action_shadow.py src/pmus_l5_action_shadow.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model.json analysis/l5/l5_action_model.json
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
RUN touch src/__init__.py

ENV PMUS_SERVICE_ROLE=ws-recorder \
    PMUS_WS_ROOT=/state/pmus-ws \
    PMUS_WS_LEAGUES=all \
    PMUS_WS_MAX_MARKETS=40 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.pmus_ws_evidence_recorder"]
