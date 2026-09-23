# THE PRODUCTION PATH for the faithful L5 probe on Polymarket US BTC15m.
#
#   L5 realtime activity -> AUTHENTICATED PMUS BTC15m book (markets WS)
#     -> L5_NATIVE -> PMUS_TRANSLATION (both frozen at 030d8bf)
#     -> SAFETY_OVERLAY -> PmusTransport
#
# This is the process that will place the probe's orders after owner
# authorization, running now exactly as it will then. It is NOT a shadow.
#
# STARTS DISARMED AND STAYS DISARMED WITHOUT THE OWNER. PmusTransport refuses
# every submit and cancel until BOTH (a) the full read-only preflight PASSED in
# this process for the current window's market, and (b) the owner's arm token
# is present and matches the session id (PMUS_Q1_LIVE=YES, PMUS_Q1_SESSION_ID,
# PMUS_Q1_ARM == PMUS_Q1_SESSION_ID). This image sets none of those. Each
# refused intent is recorded as NOT_ARMED on its provenance record.
#
# Kill switch: set L5_LIVE_HALT to any value -- deadman now, never arm.
#
# PROBE LIMITS (account protection only, P38 manifest): $5 per market, $5
# total exposure over the probe's life, 10 shares max residual, 1 market,
# $5 daily realized-loss halt; worst case $5.1738 from the fee implementation.
#
# The import closure is exactly the files below, walked from the entrypoint
# and verified to import in an isolated tree containing only them.
#
# DEPLOYMENT ISOLATION (deploy/RAILWAY_ISOLATION.md): pin
#     RAILWAY_DOCKERFILE_PATH=/deploy/l5_pmus_live/Dockerfile
# on the service; the role is BAKED below and asserted at startup.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"

COPY src/__init__.py src/__init__.py
COPY src/l5_pmus_live_runner.py src/l5_pmus_live_runner.py
# frozen at 030d8bf, hash-guarded by tests/test_faithful_rule_frozen.py
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY src/l5_live_execution.py src/l5_live_execution.py
COPY src/l5_pmus_transport.py src/l5_pmus_transport.py
COPY src/pmus_btc15m_preflight.py src/pmus_btc15m_preflight.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_fees.py src/pmus_fees.py
# the authenticated book: the recorder's own parser and signing
COPY src/l5_pmus_auth_book.py src/l5_pmus_auth_book.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
# L5's realtime activity feed and action key (read-only public GETs)
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py

ENV PMUS_SERVICE_ROLE=l5-pmus-live \
    L5_LIVE_ROOT=/state/l5-live \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.l5_pmus_live_runner"]
