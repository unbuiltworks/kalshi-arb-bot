# Probe 04 recovery: GET only, and structurally so.
#
# This image contains exactly one HTTP object -- pmus_auth.ReadOnlyTransport,
# which raises PermissionError on any method but GET. l5_pmus_transport is
# DELIBERATELY absent: it owns _SignedHTTP, which can POST. An image that
# cannot mutate is better than one that merely declines to.
#
# Also absent: every executor, order body, preview gate, authorization ledger
# and inventory store. There is nothing here that could place, cancel or
# record an order even if it were asked to.
FROM python:3.11-slim

WORKDIR /app

# signing only. websockets is NOT installed: this image subscribes to
# nothing and the venue's Ed25519 signature is all it needs.
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py src/__init__.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY scripts/__init__.py scripts/__init__.py
COPY scripts/pmus_readonly_recovery.py scripts/pmus_readonly_recovery.py
# Authoritative venue order-history audit. GET only, same ReadOnlyTransport.
COPY scripts/pmus_order_history_audit.py scripts/pmus_order_history_audit.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.pmus_readonly_recovery"]
