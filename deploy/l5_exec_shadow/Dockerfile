# L5 EXECUTABLE DUAL-VENUE SHADOW -- read-only, 24/7, no credentials.
#
# It answers one question continuously, for both BTC5m and BTC15m: at the
# moment an L5 action became visible to us, what could Polymarket and Gemini
# each ACTUALLY supply, at what all-in cost, and which venue (or neither)
# should have taken it.
#
# READ ONLY BY CONSTRUCTION. Unauthenticated GETs to public endpoints only.
# The image copies no executor, no signer, no credential reader and no order
# verb, and tests/test_l5_executable_shadow.py asserts that no order path is
# importable from this package.
#
# It is deliberately a SEPARATE image and service from:
#   l5-fidelity        (the prospective behavioural experiment)
#   l5-shadow-loop     (the older proposal/drift loop, src/l5_shadow_loop.py)
#   pmus-spring-l5-probe (the money-moving executor)
# so that a change here can never rebuild any of them.
FROM python:3.11-slim
WORKDIR /app
COPY src/__init__.py src/__init__.py
COPY src/l5_shadow/ src/l5_shadow/
COPY src/l5_intent/ src/l5_intent/
COPY src/l5_router/ src/l5_router/
COPY src/l5_fidelity/ src/l5_fidelity/
COPY artifacts/l5_fidelity/ artifacts/l5_fidelity/
# Only the two adapter modules this service imports. `evaluate.py` is
# DELIBERATELY absent: it imports `src.l5_venue_adapter`, which lazily reaches
# `pmus_q1_executor` -- the order path. Shipping it would put the executor one
# attribute lookup from a read-only image, and the package re-exports it
# lazily so nothing here touches it.
COPY src/l5_adapters/__init__.py src/l5_adapters/__init__.py
COPY src/l5_adapters/base.py src/l5_adapters/base.py
COPY src/l5_adapters/gemini_mapping.py src/l5_adapters/gemini_mapping.py
# Gemini's vendored fee schedule only.
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.l5_shadow.service", "--hours", "0", \
     "--out-dir", "/state/l5-exec-shadow"]
