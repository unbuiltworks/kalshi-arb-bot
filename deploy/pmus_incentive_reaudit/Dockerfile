# PMUS INCENTIVE RE-AUDIT. READ-ONLY. GET requests only.
#
# Closes the three-week measurement hole after
# 2026-09-14T22:59:21.565847915Z and reconciles Attempt 6's fill
# (order CYKE4XSQ0YGW) for settlement, commission and liquidity role.
#
# This image is NOT order-capable by intent AND by omission: it does not
# COPY l5_pmus_transport, pmus_micro_executor, or anything under
# analysis/l5/live_exec*. scripts/pmus_incentive_reaudit refuses, before
# issuing a single request, if PMUS_Q1_LIVE / PMUS_Q1_ARM /
# PMUS_Q1_SESSION_ID / PMUS_Q1_GATE_EVIDENCE is set.
#
# pmus_q1_executor IS copied, because signed_headers lives in it and the
# audit needs signed GETs. That is the one order-capable module present;
# the runner's arming refusal is what makes it inert.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py src/__init__.py
COPY src/pmus_auth.py src/pmus_auth.py
# NO ORDER PATH IN THIS IMAGE. The real Q1 executors carry POST
# /v1/order/preview, POST /v1/orders and POST /v1/order/{id}/cancel, so
# they are NOT copied. A GET-only shim is copied over both module names
# instead; its transport raises on any method except GET before a socket
# opens. There is no POST here to disable.
COPY deploy/pmus_incentive_reaudit/q1_getonly_shim.py src/pmus_q1_executor.py
COPY deploy/pmus_incentive_reaudit/q1_getonly_shim.py src/pmus_q1_executor_v4.py
# pmus_settlement_auditor imports q1_executor_v4 at module level for its
# paced GET helper (_get_with_retry, which hardcodes GET and never retries
# a mutation). l5_realized_bridge is imported lazily inside the audit for
# the ESTIMATED block only. Both are present only to satisfy imports; the
# runner's arming refusal is what keeps the image inert.
COPY src/l5_realized_bridge.py src/l5_realized_bridge.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_fee_schedule.py src/pmus_fee_schedule.py
COPY src/pmus_incentive_audit.py src/pmus_incentive_audit.py
COPY src/pmus_settlement_auditor.py src/pmus_settlement_auditor.py

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/pmus_incentive_reaudit.py scripts/pmus_incentive_reaudit.py

COPY analysis/__init__.py analysis/__init__.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.pmus_incentive_reaudit", "--out", "/tmp/PMUS_INCENTIVES_REAUDIT.json"]
