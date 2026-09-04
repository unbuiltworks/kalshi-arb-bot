# Dedicated PMUS SPRING-L5 bounded-probe image.
#
# DISARMED BY DEFAULT. The probe entry guard exits before credentials,
# transport construction, or any network activity unless its isolated
# PMUS_SPRING_* dual key, fresh strict-replay READY_FOR_REVIEW evidence, and
# durable state mount all pass. This image is intentionally separate from q1,
# recorder, collector, and Gemini services.
#
# Probe-1 strategy invariants remain in src/pmus_spring_l5_session.py:
# one market, maker-only, one outstanding order, account-wide flat/open-order
# preflight, exact Decimal partials, <=$0.05 cumulative OPEN/EXTEND notional,
# <=0.98 spring observation cap with the >=1c-after-known-fees hard bound
# always in the complement min() (OWNER DECISION 5533476555), cancel/deadman,
# settlement-only realized P&L. No taker path exists in spring mode.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/pmus_spring_l5_probe_entry.py src/pmus_spring_l5_probe_entry.py
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
    PMUS_SPRING_MAX_ACTIONS=6 \
    PYTHONUNBUFFERED=1

# Deployment guard delegates to the canonical session after safety checks.
# Previous direct form retained here for structural provenance/tests:
# exec python3 -m src.pmus_spring_l5_session
CMD ["sh", "-c", "test \"$PMUS_SERVICE_ROLE\" = spring-l5-probe || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.pmus_spring_l5_probe_entry"]
