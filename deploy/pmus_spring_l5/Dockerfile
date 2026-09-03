# Dedicated PMUS SPRING-L5 bounded-probe image.
#
# DISARMED BY DEFAULT. The spring module exits DISARMED before credentials,
# transport construction, or any network activity unless its isolated
# PMUS_SPRING_* dual key is explicitly supplied. This image is intentionally
# separate from the q1, recorder, collector, and Gemini services.
#
# Probe-1 invariants are preserved by src/pmus_spring_l5_session.py:
# one market, maker-only, one outstanding order, account-wide flat/open-order
# preflight, exact Decimal partials, <=$0.05 cumulative OPEN/EXTEND notional,
# fee-safe <=0.96 spring completion target, cancel/deadman, and settlement-only
# realized P&L. No taker path exists in spring mode.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/pmus_spring_l5_session.py src/pmus_spring_l5_session.py
COPY src/pmus_micro_executor.py src/pmus_micro_executor.py
COPY src/pmus_q1_executor_v4.py src/pmus_q1_executor_v4.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
RUN touch src/__init__.py

ENV PMUS_SERVICE_ROLE=spring-l5-probe \
    PMUS_Q1_STATE_DIR=/state/pmus-spring \
    PMUS_SPRING_MAX_ACTIONS=5 \
    PYTHONUNBUFFERED=1

# Repo-side deployment-isolation tripwire. A wrong service/image selection
# exits visibly instead of silently starting an unintended worker.
CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = spring-l5-probe || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.pmus_spring_l5_session"]
