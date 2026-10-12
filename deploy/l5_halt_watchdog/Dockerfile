# The independent watchdog. It can GET, and it can cancel ONE named order.
#
# What is in this image is the whole safety argument, so it is spelled out:
#
#   src/pmus_auth.py        signing, and ReadOnlyTransport
#   src/l5_halt_gate.py     the latch: asserter (no clear), gate, FileStore
#   src/l5_halt_server.py   GET-only HTTP for the executor to read the latch
#   src/l5_watchdog.py      CancelOnlyTransport + the sweep
#
# DELIBERATELY ABSENT: l5_pmus_transport (it owns _SignedHTTP, which can POST
# an order), l5_live_execution (the executor and its ledger), every order
# body, every preview gate and every arming key. This image cannot place an
# order because it does not contain the code to, and it cannot clear a halt
# because HaltAsserter has no such method.
#
# It needs a VOLUME at /data. The latch has to outlive a restart -- a halt
# held in memory is cleared by the crash it was asserting about.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py          src/__init__.py
COPY src/pmus_auth.py         src/pmus_auth.py
COPY src/l5_halt_gate.py      src/l5_halt_gate.py
COPY src/l5_halt_server.py    src/l5_halt_server.py
COPY src/l5_watchdog.py       src/l5_watchdog.py
# the ownership contract's constants (REGISTRY_KEY). The watchdog imports it
# to name the key it reads; it serves nothing and holds no venue route.
COPY src/l5_order_registry_server.py src/l5_order_registry_server.py
COPY scripts/__init__.py      scripts/__init__.py
COPY scripts/l5_halt_watchdog.py scripts/l5_halt_watchdog.py

ENV PYTHONUNBUFFERED=1
ENV L5_WATCHDOG_STATE_DIR=/data
# Cancel needs BOTH L5_WATCHDOG_CANCEL=YES and L5_ORDER_REGISTRY_URL pointing
# at the executor's read-only ownership registry. Without the registry the
# power is UNAVAILABLE, not merely off: on a shared account there is nothing
# else that tells our order from the owner's manual one.
CMD ["python3", "-m", "scripts.l5_halt_watchdog"]
