# GEMINI BTC15m CONTINUOUS SHADOW -- read-only, disarmed, no order path.
#
# It runs the SAME lifecycle module the 5-minute lane runs. The contract
# family is a PARAMETER (--cohort btc15m), not a second pipeline: discovery,
# the entry band and the wait budget all come from
# src/gemini_execution/contract_cohort.py, and the frozen policy's own TTE
# bounds are untouched on both families.
#
# NOTHING HERE CAN BE ARMED. shadow_soak constructs no session and never
# references Driver.place or Driver.cancel -- a test asserts that as a
# property of the module's AST, not as a flag it sets. There is no
# authorization id, no arming env and no live cohort binding anywhere in this
# image.
FROM python:3.11-slim
WORKDIR /app
# THE IMPORT CLOSURE, COMPUTED NOT GUESSED. The first image copied four of
# these and crash-looped on `No module named src.l5_surface`, which
# continuous_probe imports at module scope. This list is the transitive
# closure of `from src.X` / `import src.X` across the lane, including the
# lazily-imported ones an import-time trace alone would miss.
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
RUN touch src/__init__.py && pip install --no-cache-dir httpx websockets
ENV PYTHONUNBUFFERED=1 \
    GEMINI_SHADOW_ROLE=gemini-btc15m-shadow \
    GEMINI_CANARY_STATE_DIR=/state/gemini-btc15m
# --windows 0 rolls continuously: one contract after another, for ever.
CMD ["python3", "-m", "src.gemini_execution.shadow_soak", \
     "--cohort", "btc15m", "--policy", "GEMINI_RECYCLE", \
     "--windows", "0", "--cycles", "110", "--interval", "8.0", \
     "--evidence-dir", "/state/gemini-btc15m/continuous_v1", \
     "--out", "/state/gemini-btc15m/btc15m_continuous.json"]
