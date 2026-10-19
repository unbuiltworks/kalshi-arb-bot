# The independent watchdog. It can GET, and it can cancel ONE named order.
#
# What is in this image is the whole safety argument, so it is spelled out:
#
#   src/l5_halt_gate.py     the latch: asserter (no clear), gate, FileStore
#   src/l5_halt_server.py   GET-only HTTP for the executor to read the latch
#   src/l5_watchdog.py      RegistryOrderSource + the sweep
#
# AUTHENTICATED CANCEL-ONLY (OWNER 2026-10-18). This REVERSES the
# credential-free rule of 2026-10-14, by explicit owner approval, and the
# reason is on the record. On 2026-10-16T03:40:12Z order D4EFZNW00ZZ0 rested
# on cpcxrp-xrp-updown-15m-2026-10-16-0330z with no readable age and no
# leavesQuantity, and this watchdog could only LATCH it. The latch's own
# meaning is that the executor was not managing that order -- so leaving
# cancellation with the executor left exactly one order that nobody present
# could withdraw. An independent canceller is the hole that incident opened.
#
# WHAT CARRIES THE ISOLATION NOW, since it is no longer credential absence:
#
#   * CancelOnlyTransport.request refuses every method and route except GET
#     and a single POST /v1/order/{id}/cancel, raising MutationRefused. The
#     refusal lives in the transport, not in a caller's flag, so a process
#     that cannot construct a submit cannot be argued into one.
#   * Cancel needs BOTH L5_WATCHDOG_CANCEL=YES *and* an OwnershipOracle over
#     the executor's durable registry, and that claim must be FRESH
#     (MAX_CLAIM_AGE_S). A stale, unreadable or future-stamped claim yields
#     no owned ids, so a wedged executor cannot have its last snapshot
#     believed. No oracle, no POST.
#   * An id absent from the registry is never cancelled, and the venue must
#     corroborate market and manualOrderIndicator AUTOMATIC before any POST.
#     That is what protects the owner's manual orders on a shared account.
#   * Without credentials the process falls back to the credential-free
#     registry-only mode and cancels nothing. Fail-closed is unchanged.
#   * HaltAsserter still has no clear method: this image cannot lift a halt.
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

# cryptography signs the ONE cancel route. pmus_auth imports it lazily inside
# the signing function, so an image without it still boots and still latches.
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py          src/__init__.py
# the Ed25519 signature, and nothing else. pmus_auth holds no order body and
# no submit route; CancelOnlyTransport is what decides which routes exist.
COPY src/pmus_auth.py         src/pmus_auth.py
COPY src/l5_halt_gate.py      src/l5_halt_gate.py
COPY src/l5_halt_server.py    src/l5_halt_server.py
COPY src/l5_watchdog.py       src/l5_watchdog.py
# the ownership contract's constants (REGISTRY_KEY). The watchdog imports it
# to name the key it reads; it serves nothing and holds no venue route.
COPY src/l5_order_registry_server.py src/l5_order_registry_server.py
COPY scripts/__init__.py      scripts/__init__.py
COPY scripts/l5_halt_watchdog.py scripts/l5_halt_watchdog.py
# THE OWNER-GATED HALT RESET, and why it is HERE rather than in a one-shot
# image. The latch is a file on this service's volume and a Railway volume
# attaches to exactly one service, so no other process can write it. Railway
# runs `preDeployCommand` in the NEW deployment's container with the volume
# mounted while the CURRENT watchdog keeps sweeping, so the clear happens
# WITHOUT the independent watchdog ever going offline -- which is the whole
# point, and the opposite of the image swap that took it down for 5m53s on
# 2026-10-19.
#
# THIS DOES NOT GIVE THE SWEEP A CLEAR PATH. The sweep loop never calls it;
# `HaltAsserter` still has no clear method; `heartbeat_no_halt` still refuses
# over a latch. This image ALREADY held permissive-write capability through
# that method -- "cannot clear" was always a GUARD, never an absence of code.
# What is added is a SEPARATE process, invoked by the platform, behind four
# fail-closed owner gates, whose reset id is spent by the ATTEMPT so a
# leftover pre-deploy command clears nothing on any later deployment.
COPY scripts/l5_halt_reset.py scripts/l5_halt_reset.py

ENV PYTHONUNBUFFERED=1
ENV L5_WATCHDOG_STATE_DIR=/data
# the image names itself, so a redeploy that silently reused another build is
# detectable. Deliberately NOT the one-shot image's value.
ENV L5_RESET_IMAGE=l5_halt_watchdog
# L5_ORDER_REGISTRY_URL names the executor's GET-only report AND is the
# ownership oracle: an id absent from it is never cancelled, which is how the
# owner's manual orders stay untouched on a shared account. Losing this
# variable removes the cancel capability entirely, even with credentials and
# L5_WATCHDOG_CANCEL=YES. Without PMUS credentials the process takes the
# credential-free branch and cancels nothing.
CMD ["python3", "-m", "scripts.l5_halt_watchdog"]
