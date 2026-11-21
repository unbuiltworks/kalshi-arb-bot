# L5_V1_1 -- SHADOW ONLY, and it cannot trade for the same structural reason
# the V1 image cannot: no executor, no transport, no fill ledger, no order verb,
# and the service is deployed with NO API CREDENTIALS.
#
# It ships the V1 module too, because Stage 2 IS V1 -- unchanged, just finally
# given the last_rel/last_size it always accepted and never received.
#
# Stage 1 (ACT/WAIT) and Stage 3 (TIMING) come from l5_act_wait_v1.json, a
# SEPARATE versioned artifact. The frozen l5_action_model_v2.json is copied
# read-only and is not modified by anything in this image.
FROM python:3.11-slim

WORKDIR /app

COPY src/l5_v1_shadow.py src/l5_v1_shadow.py
COPY src/l5_v1_shadow_runner.py src/l5_v1_shadow_runner.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
# the DEPLOYED v2 loader. The shadow scores against the same shrinkage every
# other consumer uses, so a disagreement is L5's and not a parsing artefact.
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
COPY analysis/l5/live_v1/__init__.py analysis/l5/live_v1/__init__.py
COPY analysis/l5/live_v1/risk_scale.py analysis/l5/live_v1/risk_scale.py
COPY src/l5_v1_1_shadow.py src/l5_v1_1_shadow.py
COPY src/l5_v1_1_shadow_runner.py src/l5_v1_1_shadow_runner.py
COPY analysis/l5/act_wait/__init__.py analysis/l5/act_wait/__init__.py
COPY analysis/l5/act_wait/l5_act_wait_v1.json analysis/l5/act_wait/l5_act_wait_v1.json
RUN touch src/__init__.py analysis/__init__.py analysis/l5/__init__.py

ENV PMUS_SERVICE_ROLE=l5-v1-1-shadow \
    L5_V1_1_STATE_DIR=/state/l5-v1-1-shadow \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = l5-v1-1-shadow || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.l5_v1_1_shadow_runner"]
