# Keyless READ-ONLY Polymarket US sports forward collector.
# Isolated service: public gateway.polymarket.us GETs only — no API keys,
# no authenticated endpoints, no order path. See #167 comment 5514571607.
#
# Build (repo root):
#   docker build -f deploy/pmus_sports_collector/Dockerfile -t pmus-collector .
# Run:
#   docker run -d --name pmus-collector \
#     -v pmus_state:/state/pmus-sports \
#     pmus-collector
#
# Persistence note: /state/pmus-sports is durable ONLY if a volume is
# mounted there by the deployment; container-local otherwise.
FROM python:3.11-slim

WORKDIR /app
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_status_report.py src/pmus_status_report.py
RUN touch src/__init__.py

ENV PMUS_COLLECTOR_ROOT=/state/pmus-sports \
    PMUS_COLLECTOR_LEAGUES=mlb \
    PMUS_COLLECT_INCENTIVE_MARKETS=1 \
    PMUS_POLL_SECONDS=5 \
    PMUS_RATE_BUDGET_RPS=8 \
    PMUS_MAX_MARKETS=24 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.pmus_sports_collector"]
