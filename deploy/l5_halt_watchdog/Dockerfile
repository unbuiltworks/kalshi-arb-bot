# The independent watchdog. It can GET, and it can cancel ONE named order.
#
# What is in this image is the whole safety argument, so it is spelled out:
#
#   src/l5_halt_gate.py     the latch: asserter (no clear), gate, FileStore
#   src/l5_halt_server.py   GET-only HTTP for the executor to read the latch
#   src/l5_watchdog.py      RegistryOrderSource + the sweep
#
# CREDENTIAL-FREE (OWNER 2026-10-14). src/pmus_auth.py is now DELIBERATELY
# ABSENT too, which is the whole point: this image contains no request-signing
# code, so it cannot reach the venue however it is configured. That is
# permission-level isolation achieved by holding no credential to scope,
# rather than by restricting routes on a credential it holds. The watchdog
# judges the executor from the executor's OWN GET-only report over private
# networking, and cancellation stays with the executor under its own
# credentials. CancelOnlyTransport refuses to be constructed without
# pmus_auth, so the capability is unavailable rather than switched off.
#
# ALSO DELIBERATELY ABSENT: l5_pmus_transport (it owns _SignedHTTP, which can
# POST an order), l5_live_execution (the executor and its ledger), every order
# body, every preview gate and every arming key. This image cannot place an
# order because it does not contain the code to, and it cannot clear a halt
# because HaltAsserter has no such method.
#
# It needs a VOLUME at /data. The latch has to outlive a restart -- a halt
# held in memory is cleared by the crash it was asserting about.
FROM python:3.11-slim

WORKDIR /app

# NO cryptography: nothing here signs a request. It was the only compiled
# dependency, so the image is smaller and the attack surface is the stdlib.

COPY src/__init__.py          src/__init__.py
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
# L5_ORDER_REGISTRY_URL names the executor's GET-only report and selects
# credential-free mode. Cancel is UNAVAILABLE in that mode -- not switched
# off: there is no signing code to cancel with, and on a shared account
# nothing but the submitter can tell our order from the owner's manual one.
CMD ["python3", "-m", "scripts.l5_halt_watchdog"]
