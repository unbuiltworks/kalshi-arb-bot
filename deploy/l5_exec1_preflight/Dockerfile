# LIVE-L5-EXEC-1 boot preflight image. DISARMED, and MUTATION-INCAPABLE.
#
# This image carries the exact frozen EXEC-1 artifact (spec + ledger, both
# unmodified) plus a read-only boot preflight, and nothing that can place,
# amend or cancel an order.
#
# DELIBERATELY ABSENT, and the preflight FAILS if any of them turns out to
# be importable:
#   src/l5_pmus_transport.py   -- owns _SignedHTTP, which can POST
#   src/pmus_q1_executor.py    -- owns place_limit_buy / run_campaign
#   src/pmus_micro_executor.py -- the other order path
#
# The only HTTP object here is pmus_auth.ReadOnlyTransport, which raises
# PermissionError on any method but GET. An image that CANNOT mutate beats
# one that merely declines to, and "DISARMED" is a flag someone can flip.
#
# Attempt 2 therefore cannot be produced by setting an environment variable
# on this image: it needs a separate, separately authorized image that adds
# the order path.
FROM python:3.11-slim

WORKDIR /app

# Ed25519 signing for authenticated GETs; websockets for the AuthBook feed.
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
COPY analysis/l5/live_micro/spec.py analysis/l5/live_micro/spec.py
COPY analysis/l5/live_micro/ledger.py analysis/l5/live_micro/ledger.py
COPY analysis/l5/s3/spec.py analysis/l5/s3/spec.py

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_exec1_boot_preflight.py scripts/l5_exec1_boot_preflight.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.l5_exec1_boot_preflight"]
