# THE INDEPENDENT READ-ONLY E1 SAFETY MONITOR. Three files, GET only.
#
# This image exists to watch the one-window E1 probe and alert a human if it
# leaves an order unmanaged. It is built to be structurally incapable of
# doing anything else:
#
#   * the only venue HTTP object it contains is
#     `pmus_auth.ReadOnlyTransport`, which raises PermissionError on any
#     method but GET;
#   * `src.l5_pmus_transport` is DELIBERATELY ABSENT -- it owns the signed
#     HTTP class that can POST;
#   * no executor, no order body, no preview gate, no latch, no
#     authorization ledger, no cohort package. There is nothing here that
#     could place or cancel an order even if it were asked to, and the
#     monitor asserts that at boot by importing those module names and
#     reporting that they are missing;
#   * NO VOLUME is mounted on this service, so it cannot read or write E1's
#     tape, latch or progress ledger.
#
# It also refuses to start if any arming key is present in its own
# container: a monitor that shares an authorization environment with an
# executor is not independent of it.
FROM python:3.11-slim

WORKDIR /app

# signing only. No websockets: this monitor subscribes to nothing.
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py src/__init__.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_e1_safety_monitor.py scripts/l5_e1_safety_monitor.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.l5_e1_safety_monitor"]
