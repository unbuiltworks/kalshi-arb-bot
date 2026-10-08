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
COPY scripts/pmus_e1_postcancel_confirm.py scripts/pmus_e1_postcancel_confirm.py
COPY scripts/l5_pmus_order_endpoint_probe.py scripts/l5_pmus_order_endpoint_probe.py
COPY scripts/l5_e1_1215z_reconcile.py scripts/l5_e1_1215z_reconcile.py
COPY scripts/l5_pmus_activities_param_probe.py scripts/l5_pmus_activities_param_probe.py
# The settlement audit: filtered, resumable, 429-aware.
#
# EXACTLY THREE FILES, not the package. Copying analysis/l5/e1_cohort/
# wholesale would drag in runner.py -- and with it live_exec2.orders, the
# order body and the mutating BoundedOrderTransport -- which would destroy
# the one property this image is built for: it cannot place an order
# because it does not contain the code to. order_state imports only
# decimal and typing, and reaches src.pmus_auth lazily; settlement_audit
# imports only order_state. Nothing else is needed and nothing else comes.
COPY analysis/__init__.py             analysis/__init__.py
COPY analysis/l5/__init__.py          analysis/l5/__init__.py
COPY analysis/l5/e1_cohort/__init__.py analysis/l5/e1_cohort/__init__.py
COPY analysis/l5/e1_cohort/order_state.py \
     analysis/l5/e1_cohort/order_state.py
COPY analysis/l5/e1_cohort/settlement_audit.py \
     analysis/l5/e1_cohort/settlement_audit.py
COPY scripts/l5_e1_settlement_audit_live.py \
     scripts/l5_e1_settlement_audit_live.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.pmus_readonly_recovery"]
