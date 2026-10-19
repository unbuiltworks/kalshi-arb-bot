# THE ONE-SHOT ADMIN IMAGE. It exists to run exactly one command and exit.
#
# WHY IT IS SEPARATE (OWNER 2026-10-15). The HALT latch is a file on the
# watchdog's volume, and a Railway volume mounts to exactly ONE service, so
# the only process that can read or write that latch is one running in
# l5-halt-watchdog. A one-time reset therefore needs, briefly, two things the
# ordinary watchdog deliberately does NOT have: venue credentials for the
# authenticated read-only checks, and a code path that writes the halt object.
#
# Putting those in the watchdog's own image would make a PERMANENTLY
# privileged watchdog to serve a one-time need, and would reverse the
# credential-free decision of 2026-10-14 for good. This image is the
# alternative: the watchdog service is pointed at this Dockerfile for the
# duration of the reset and then pointed back, so the privilege is
# TEMPORARY and the continuously running watchdog image is untouched.
#
# WHAT IT CAN AND CANNOT DO.
#   * It can GET from the venue, signed: pmus_auth.ReadOnlyTransport raises
#     PermissionError on any method but GET. There is no cancel route and no
#     submit route in this entrypoint's reach.
#   * It can write the ONE halt object on the volume, which is the whole
#     point, and only after four fail-closed gates.
#   * l5_pmus_transport (it owns _SignedHTTP, which can POST an order),
#     l5_live_execution, l5_live_authorization, every order body and every
#     arming key stay ABSENT. It cannot place an order because the code to
#     do so is not here.
#   * It never sweeps and never heartbeats, so while it is the running
#     configuration the watchdog is NOT protecting anything. That is why the
#     procedure restores the ordinary image immediately and verifies fresh
#     sweeps before anything is armed.
#
# CREDENTIALS ARE TEMPORARY AND MUST BE REMOVED. They are set on the service
# for the reset and deleted straight after; the procedure verifies their
# absence. Nothing here prints, logs or returns a secret: the reset's output
# carries the latch, the evidence and the audit entry, never a credential.
#
# restartPolicyType must be NEVER while this is the start command. The script
# exits by design, and ALWAYS would restart it into a loop -- harmless,
# because the reset id is spent by the first attempt and every later boot
# refuses RESET_ID_ALREADY_CONSUMED, but it is noise in the logs and leaves
# the service not-sweeping for longer.
FROM python:3.11-slim

WORKDIR /app

# Ed25519 signing for the authenticated read-only venue checks.
RUN pip install --no-cache-dir "cryptography>=42"

COPY src/__init__.py          src/__init__.py
COPY src/l5_halt_gate.py      src/l5_halt_gate.py
# is_terminal() and RegistryOrderSource, reused rather than reimplemented:
# duplicating the "is this order still working" rule is how two answers to
# one question get out of step.
COPY src/l5_watchdog.py       src/l5_watchdog.py
# signing + ReadOnlyTransport (GET-only). No mutating route.
COPY src/pmus_auth.py         src/pmus_auth.py
COPY src/l5_order_registry_server.py src/l5_order_registry_server.py
COPY scripts/__init__.py      scripts/__init__.py
COPY scripts/l5_halt_reset.py scripts/l5_halt_reset.py

ENV PYTHONUNBUFFERED=1
ENV L5_WATCHDOG_STATE_DIR=/data
# INSPECT is the default: a boot of this image with nothing else set performs
# the READ-ONLY dry run and writes nothing. Clearing requires the confirm
# phrase, the digest, the latch's own reason, an operator and a fresh id.
ENV L5_HALT_RESET_INSPECT=YES
# THE IMAGE NAMES ITSELF, and no other image carries this. scripts.l5_halt_reset
# refuses to CLEAR without it. On 2026-10-19 a `redeploy` reused the previous
# WATCHDOG build after dockerfilePath had been pointed here, so the service
# config claimed the reset image while the watchdog was what actually ran.
# Service config is not evidence of what is running; a marker inside the image
# is. See _require_reset_image.
ENV L5_RESET_IMAGE=l5_halt_reset
CMD ["python3", "-m", "scripts.l5_halt_reset"]
