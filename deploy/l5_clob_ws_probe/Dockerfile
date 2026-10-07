# DUAL-TOKEN CLOB WEBSOCKET PROBE. READ-ONLY. NEVER TRADES. SHORT-LIVED.
#
# Exists for ONE reason: the websocket endpoint cannot be settled from the
# research container, whose egress proxy returns 403 for every resolving
# Polymarket websocket host. Railway reaches the venue, so the handshake is
# proven there.
#
# It is a SEPARATE service with NO VOLUME. It must not disturb the running
# REST collector (service 119b038c), which is the standing instruction:
#   - it does not share the collector's volume, so it cannot write the tape;
#   - it does not redeploy the collector;
#   - it exits after the probe rather than running continuously.
#
# Same structural refusal as the collector: the only frames it can send are
# subscribe, pong and close (guard_outbound), and the boot gate fails if any
# order-capable module turns out to be importable.
FROM python:3.11-slim
WORKDIR /app

COPY src/l5_book_shadow.py src/l5_book_shadow.py
COPY src/l5_clob_ws.py src/l5_clob_ws.py
COPY scripts/l5_clob_ws_run.py scripts/l5_clob_ws_run.py
RUN touch src/__init__.py scripts/__init__.py

ENV PYTHONUNBUFFERED=1
# --probe-only: prove the contract and exit. It does NOT accumulate a tape;
# an unproven subscription whose gaps cannot be told from quiet books is
# worse than no tape at all.
CMD ["python3", "-m", "scripts.l5_clob_ws_run", "--probe-only", \
     "--probe-seconds", "25"]
