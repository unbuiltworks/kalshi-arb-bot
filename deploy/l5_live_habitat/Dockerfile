# Durable READ-ONLY live-sports habitat + frozen-L5-shadow collector.
#
# Exists because the research container recycled three times in two hours on
# 2026-09-20, each time killing every detached collector. This image holds the
# collection loops only.
#
# ISOLATION BY CONSTRUCTION: the entrypoint module imports nothing but the
# Python standard library. No executor, no session, no signer, no credential
# reader and no order transport can enter this image, because none is copied
# into it. There is no arming flag and nothing to arm.
#
# Persistence: L5_LIVE_ROOT is durable ONLY with a mounted volume.
FROM python:3.11-slim

WORKDIR /app
COPY src/l5_live_habitat_service.py src/l5_live_habitat_service.py
RUN touch src/__init__.py

ENV L5_LIVE_ROOT=/state/l5-live \
    L5_PMUS_BOOK_CAD=8 \
    L5_INTL_BOOK_CAD=8 \
    L5_PMUS_FLOW_CAD=8 \
    L5_INTL_FLOW_CAD=20 \
    L5_REDISCOVER_S=180 \
    L5_HEARTBEAT_S=60 \
    L5_PMUS_CAP=300 \
    L5_INTL_CAP=150 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.l5_live_habitat_service"]
