# LIVE-L5-EXEC-1-X execution image. ORDER-CAPABLE, and DISARMED.
#
# Unlike deploy/l5_exec1_preflight/, this image CAN change venue state. So
# "disarmed" is a claim it has to prove at every boot rather than a property
# of what was left out, and scripts/l5_exec2_boot_preflight.py proves it
# three ways: the four PMUS_EXEC1_* keys absent, the REAL submission
# preflight returning ok=False with DISARMED on the actual code path, and the
# bounded transport refusing eight foreign routes.
#
# WHAT IT CAN DO: four POST routes, and only four --
#   /v1/order/preview   /v1/orders   /v1/order/{id}/cancel
#   /v1/orders/open/cancel
# Anything else raises MutationRefused inside the transport, before a socket
# opens. There is no generic POST here to be reused.
#
# STILL DELIBERATELY ABSENT: pmus_q1_executor, pmus_micro_executor and
# l5_pmus_transport -- the campaign machinery, the other probes' order paths
# and the generic signed transport. A blocking gate FAILS if any of them is
# importable. This build carries its own four-route path instead of
# inheriting one that can do more than this experiment needs.
#
# The durable one-submission latch lives on the mounted volume at
# /state/pmus-ws, so exactly-one-order no longer depends on
# restartPolicyType=NEVER to hold.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "cryptography>=42" "websockets>=12"

COPY src/__init__.py src/__init__.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
COPY src/l5_pmus_auth_book.py src/l5_pmus_auth_book.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py

COPY analysis/__init__.py analysis/__init__.py
COPY analysis/l5/__init__.py analysis/l5/__init__.py
COPY analysis/l5/live_exec/spec.py analysis/l5/live_exec/spec.py
COPY analysis/l5/live_exec/ledger.py analysis/l5/live_exec/ledger.py
COPY analysis/l5/live_exec2/spec.py analysis/l5/live_exec2/spec.py
COPY analysis/l5/live_exec2/latch.py analysis/l5/live_exec2/latch.py
COPY analysis/l5/live_exec2/orders.py analysis/l5/live_exec2/orders.py
COPY analysis/l5/live_micro/spec.py analysis/l5/live_micro/spec.py
COPY analysis/l5/live_micro/ledger.py analysis/l5/live_micro/ledger.py
COPY analysis/l5/s3/spec.py analysis/l5/s3/spec.py

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_exec2_boot_preflight.py scripts/l5_exec2_boot_preflight.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.l5_exec2_boot_preflight"]
