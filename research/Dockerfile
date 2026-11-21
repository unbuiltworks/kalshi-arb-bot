# Isolated READ-ONLY L5 research service (issue #167 safe activation).
#
# This is NOT the trading bot image (root Dockerfile). It runs only the
# ops runner loop: public-data collection + behavioral shadow + daily
# regression. No exchange credentials, no order transport, cannot trade.
#
# Build context = repo root of the pinned research branch:
#   docker build -f research/Dockerfile -t l5-ops .
# On Railway: create a NEW service watching feature/l5-canonical with
# "Dockerfile path" = research/Dockerfile. Do not attach it to the
# existing trading service or its environment variables.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The ops layer is stdlib-only; pytest is for the daily regression run.
RUN pip install --no-cache-dir pytest

COPY src/ src/
COPY config/ config/
COPY scripts/l5_ops_runner.sh scripts/
COPY tests/ tests/
COPY analysis/l5/l5_behavior.json \
     analysis/l5/l5_action_model.json \
     analysis/l5/l5_canonical_sequence.csv.gz \
     analysis/l5/l5_canonical_sequence.meta.json \
     analysis/l5/

# Stamp the code version so the runner reports exactly what is deployed.
ARG GIT_SHA=unknown
ENV OPS_CODE_VERSION=$GIT_SHA

CMD ["bash", "scripts/l5_ops_runner.sh"]
