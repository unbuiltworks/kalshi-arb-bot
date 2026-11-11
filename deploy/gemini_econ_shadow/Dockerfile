# GEMINI ECONOMIC CHALLENGER SHADOW -- read-only, disarmed, no order path.
#
# This is the SAME shadow_soak lifecycle the control runs, on the SAME two
# crypto cohorts. The only difference is that GEMINI_ECON_V1 is registered as
# one more arm inside it, with its own guard, inventory and hypothetical book.
#
# WHY IT IS A SECOND SERVICE RATHER THAN A REDEPLOY OF THE FIRST. The control
# `gemini-maker-shadow` has been serving a44de245 continuously and its
# watchPatterns are what have kept it pinned there while the branch moved on.
# Redeploying it to pick up the challenger would restart the control's own
# inventory walk and end the comparison this whole session exists to make. So
# the control is left exactly as it is and the challenger runs beside it,
# reading the same public tape from its own process.
#
# NOTHING HERE CAN BE ARMED. shadow_soak constructs no session and never
# references Driver.place or Driver.cancel -- asserted over the module AST by
# tests/test_gemini_shadow_soak_disarmed-style checks rather than by a flag.
# The econ package adds no transport of its own, and tests assert that its
# modules contain no order path, signer or credential.
FROM python:3.11-slim
WORKDIR /app
# The import closure, unchanged from the control's image. src/gemini_execution
# already carries the new econ/ package, so nothing is added here.
COPY src/__init__.py src/__init__.py
COPY src/gemini_execution/ src/gemini_execution/
COPY src/l5_surface/ src/l5_surface/
COPY src/l5_intent/ src/l5_intent/
COPY src/l5_shadow/ src/l5_shadow/
COPY src/l5_adapters/ src/l5_adapters/
COPY src/l5_fidelity/ src/l5_fidelity/
COPY src/l5_router/ src/l5_router/
COPY src/polymarket_l5/ src/polymarket_l5/
COPY src/gemini_prediction_auth.py src/gemini_prediction_auth.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/auth.py src/auth.py
COPY src/fee_calc.py src/fee_calc.py
COPY artifacts/l5_surface/ artifacts/l5_surface/
RUN touch src/__init__.py && pip install --no-cache-dir httpx websockets
# A SEPARATE STATE ROOT. The control owns /state/BTC5m and /state/BTC15m and
# has been writing them since 2026-09-19; the challenger must not share, or a
# restart of either would read the other's window history as its own.
ENV PYTHONUNBUFFERED=1 \
    GEMINI_SHADOW_ROLE=gemini-econ-shadow \
    GEMINI_CANARY_STATE_DIR=/state/gemini-econ \
    GEMINI_ECON_ARM=1 \
    GEMINI_ECON_STATE_DIR=/state/gemini-econ/econ-state
CMD ["python3", "-m", "src.gemini_execution.shadow_soak", \
     "--cohorts", "btc5m,btc15m", "--policy", "GEMINI_RECYCLE", \
     "--windows", "0", "--cycles", "110", "--interval", "8.0", \
     "--state-root", "/state/gemini-econ"]
