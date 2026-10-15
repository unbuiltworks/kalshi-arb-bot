# The independent watchdog. It can GET, and it can cancel ONE named order.
#
# What is in this image is the whole safety argument, so it is spelled out:
#
#   src/l5_halt_gate.py     the latch: asserter (no clear), gate, FileStore
#   src/l5_halt_server.py   GET-only HTTP for the executor to read the latch
#   src/l5_watchdog.py      RegistryOrderSource + the sweep
#
# CREDENTIALED CANCEL-ONLY (OWNER 2026-10-15). This REVERSES the
# credential-free decision of 2026-10-14, deliberately and with the cost
# stated. The owner required an INDEPENDENT cancellation backstop: a
# credential-free watchdog can assert the halt and then has to leave the
# order RESTING ("cancel is not enabled on this watchdog"), so the only
# process that could withdraw an order was the executor -- the very process
# the watchdog exists to restrain. A backstop that cannot act on the failure
# it detects is not a backstop.
#
# WHAT THAT COSTS, PLAINLY. This image now carries src/pmus_auth.py and
# therefore request-signing code, and the service holds venue credentials.
# PMUS issues ONE account-wide key pair, so credential-level scoping is NOT
# available and the isolation is enforced IN CODE instead:
#
#   * CancelOnlyTransport may GET, or POST exactly one route matched by
#     CANCEL_ROUTE = ^/v1/order/[A-Za-z0-9_-]+/cancel$. Every other method or
#     path raises MutationRefused. There is no submit verb to call.
#   * pmus_auth adds signing and ReadOnlyTransport (GET-only). It contains no
#     order body and no mutating route.
#   * l5_pmus_transport stays ABSENT -- it owns _SignedHTTP, which can POST
#     an order. So does l5_live_execution, and every preview gate and arming
#     key. This image cannot place an order because the code to do so is not
#     in it.
#   * Cancellation additionally requires TWO independent witnesses before any
#     POST: the id must appear in the executor's durable ownership registry,
#     AND the venue's own row must agree on the market and report
#     manualOrderIndicator AUTOMATIC. A MANUAL order is never cancellable by
#     this path, which is what protects the owner's hand-placed orders on
#     this shared account.
#
# IT STILL CANNOT CLEAR A HALT. HaltAsserter has no such method, and nothing
# added here changes that. The one-time owner-gated reset lives in its own
# admin entrypoint (scripts/l5_halt_reset.py) which the sweep never calls.
#
# It needs a VOLUME at /data. The latch has to outlive a restart -- a halt
# held in memory is cleared by the crash it was asserting about.
FROM python:3.11-slim

WORKDIR /app

# cryptography is required again: pmus_auth signs with Ed25519, so the cancel
# POST cannot be authenticated without it. Pinned, and the ONLY non-stdlib
# dependency in this image.
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py          src/__init__.py
COPY src/l5_halt_gate.py      src/l5_halt_gate.py
COPY src/l5_halt_server.py    src/l5_halt_server.py
COPY src/l5_watchdog.py       src/l5_watchdog.py
# signing + ReadOnlyTransport ONLY. No order body, no mutating route; the one
# POST this image can form is CancelOnlyTransport's single cancel route.
COPY src/pmus_auth.py         src/pmus_auth.py
# the ownership contract's constants (REGISTRY_KEY). The watchdog imports it
# to name the key it reads; it serves nothing and holds no venue route.
COPY src/l5_order_registry_server.py src/l5_order_registry_server.py
COPY scripts/__init__.py      scripts/__init__.py
COPY scripts/l5_halt_watchdog.py scripts/l5_halt_watchdog.py
# THE ADMIN ENTRYPOINT, never reached by the sweep. It is a separate module
# with its own main(); scripts/l5_halt_watchdog.py does not import it, so no
# automatic path can clear a latch.
COPY scripts/l5_halt_reset.py scripts/l5_halt_reset.py

ENV PYTHONUNBUFFERED=1
ENV L5_WATCHDOG_STATE_DIR=/data
# L5_ORDER_REGISTRY_URL names the executor's GET-only report, which remains
# the LIVENESS source and the ownership registry. With PMUS credentials also
# present, from_env() leaves credential-free mode and builds
# CancelOnlyTransport + OwnershipOracle; cancel then additionally requires
# L5_WATCHDOG_CANCEL=YES. Without the credentials this image still degrades
# to assert-only, which is the safe direction.
CMD ["python3", "-m", "scripts.l5_halt_watchdog"]
