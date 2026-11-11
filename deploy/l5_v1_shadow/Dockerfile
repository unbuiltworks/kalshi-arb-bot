# L5_CONTINUOUS_INVENTORY_V1 -- SHADOW ONLY.
#
# THIS IMAGE CANNOT TRADE, AND NOT BECAUSE A FLAG SAYS SO. It ships no
# executor, no transport, no fill ledger and no order verb, and the service is
# deployed with NO API CREDENTIALS. Compare the COPY set below with
# deploy/l5_r1_probe/Dockerfile: every module that can reach the venue with an
# order -- pmus_micro_executor, pmus_q1_executor*, pmus_fill_ledger,
# pmus_spring_l5_session -- is absent. A shadow whose safety rests on a
# variable is one variable away from being live; this one is not.
#
# The only network it does is a read-only GET of the recorder's status handoff
# over Railway's private network, which carries no credentials of its own.
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
RUN touch src/__init__.py analysis/__init__.py analysis/l5/__init__.py

ENV PMUS_SERVICE_ROLE=l5-v1-shadow \
    L5_V1_STATE_DIR=/state/l5-v1-shadow \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = l5-v1-shadow || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.l5_v1_shadow_runner"]
