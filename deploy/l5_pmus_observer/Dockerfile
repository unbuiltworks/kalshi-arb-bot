# READ-ONLY realtime L5 -> PMUS BTC15m copyability observer.
#
# WHY THIS SERVICE EXISTS. The observer previously ran inside the interactive
# session container, which is suspended and resumed between turns: every local
# process is destroyed, and no setsid/disown/supervisor pattern survives that
# (see analysis/TECHNICAL_DEBT.md TD-3). Windows whose L5 actions arrived
# during a suspension can never be REALTIME windows, because the PMUS state at
# receipt was never observed. This image moves the observer next to the
# evidence recorders that were unaffected, so the realtime sample accrues
# continuously instead of only while a turn is active.
#
# IT IS A CONTINUITY CHANGE ONLY. L5_NATIVE, PMUS_PERMITTED, the SAFETY_OVERLAY
# separation, the STRICT/PLAUSIBLE/COPYABILITY definitions, the authenticated
# interaction classifier, the 0.20-0.80 band, the controller, persistence,
# sizing and timing are all byte-identical to what ran locally.
#
# STRUCTURALLY INCAPABLE OF TRADING. Public GETs only over urllib: no
# credential is copied, no venue client that can mutate is copied, and no
# order/cancel/amend path exists in the import closure below. The closure is
# exactly seven modules plus one model artifact, walked from the entrypoint.
#
# DEPLOYMENT ISOLATION (deploy/RAILWAY_ISOLATION.md): services in this
# environment build from the same branch, so the per-service pin
#     RAILWAY_DOCKERFILE_PATH=/deploy/l5_pmus_observer/Dockerfile
# is required. As a repo-side tripwire the role is BAKED below and asserted at
# startup: a cross-build exits loudly with SERVICE_ROLE_MISMATCH instead of
# running the wrong worker into the wrong volume.
FROM python:3.11-slim

WORKDIR /app

# No third-party packages: stdlib urllib/ssl only. Nothing is installed that
# could carry a venue SDK with an order path into this image.

COPY src/__init__.py src/__init__.py
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/action_model_v2.py src/polymarket_l5/action_model_v2.py
# persistence-v2 resolves this artifact relative to the repo root, cwd-independent
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json

ENV PMUS_SERVICE_ROLE=l5-pmus-observer \
    L5_PMUS_OBS_ROOT=/state/l5-pmus-obs \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.l5_pmus_realtime_observer", "--minutes", "0", "--interval", "10"]
