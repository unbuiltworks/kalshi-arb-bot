# Authenticated READ-ONLY Polymarket US markets-stream recorder.
# Subscribes to wss://api.polymarket.us/v1/ws/markets (book + trade tape)
# and appends verbatim JSONL under /state/pmus-ws. The only outbound
# frames are subscribe/unsubscribe/heartbeat (guard-enforced + tested);
# no order endpoint or private stream exists in this worker.
#
# IMPORTANT (Railway): this service must set its Config File Path to
# deploy/pmus_ws_recorder/railway.json — the repo-root railway.json pins
# the keyless collector and would otherwise start the wrong worker.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_status_report.py src/pmus_status_report.py
RUN touch src/__init__.py

ENV PMUS_WS_ROOT=/state/pmus-ws \
    PMUS_WS_LEAGUES=mlb \
    PMUS_WS_MAX_MARKETS=24 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.pmus_ws_evidence_recorder"]
