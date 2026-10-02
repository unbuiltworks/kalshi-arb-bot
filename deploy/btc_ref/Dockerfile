# READ-ONLY external BTC reference collector.
#
# WHY IT IS ITS OWN IMAGE. It must not share a build with
# pmus-ws-evidence-recorder: that service holds the PMUS credentials and the
# authenticated tape the whole OOS experiment depends on, and nothing here may
# modify, restart, or disrupt it. This image therefore copies ONE file, holds no
# credential of any kind (the Coinbase public feed needs none), and contains no
# order path, no auth module, and no PMUS code.
#
# Pinned the supported way, per deploy/RAILWAY_ISOLATION.md:
#     RAILWAY_DOCKERFILE_PATH=/deploy/btc_ref/Dockerfile
# and the role tripwire below makes a cross-build fail loudly rather than
# silently recording the wrong stream.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12"
COPY src/btc_ref_recorder.py src/btc_ref_recorder.py
RUN touch src/__init__.py

ENV BTC_REF_SERVICE_ROLE=btc-ref-recorder \
    BTC_REF_EXPECT_ROLE=btc-ref-recorder \
    BTC_REF_ROOT=/state/btc-ref \
    BTC_REF_FOREVER=1 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.btc_ref_recorder", "--forever"]
