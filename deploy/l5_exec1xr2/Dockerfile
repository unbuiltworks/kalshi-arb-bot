# LIVE-L5-EXEC-1-XR2. THE RUNNER. Order-capable, DISARMED.
#
# CMD is the real runner, not a preflight: scripts.l5_exec1xr2_run calls
# runner.run_experiment, the same function that places the authorized order.
# DISARMED it walks the whole path and stops at the final pre-POST refusal
# and says so, so this deployment tests the live entrypoint.
#
# Call graph to the POST:
#   scripts.l5_exec1xr2_run:main
#     -> analysis.l5.live_exec3.runner:run_experiment
#       -> _await_window       (may start BEFORE the frozen window opens)
#       -> _tte_guard          (recheck 2 and 3; the TTE invariant)
#         -> analysis.l5.live_exec2.orders:submit_one(guard=...)
#             guard() -> live_exec2.latch:consume -> guard()
#             -> orders:BoundedOrderTransport.request -> POST /v1/orders
#
# NO ORDER SUBMISSION MAY OCCUR WHEN TTE <= 120.0 SECONDS. Checked at run
# start, immediately before the latch, and immediately before the POST.
#
# Four POST routes exist and no others. Still deliberately absent, with a
# gate that reports them if importable: pmus_q1_executor,
# pmus_micro_executor, l5_pmus_transport.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "cryptography>=42" "websockets>=12"

COPY src/__init__.py src/__init__.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
# THE PROVEN VENUE SERIALIZER. Required since the hand-rolled body
# was deleted. It carries serialization ONLY -- no credentials, no
# transport, no endpoint, no executor import -- so copying it adds
# NO order path to this image beyond the four permitted routes.
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_pmus_auth_book.py src/l5_pmus_auth_book.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py

COPY analysis/__init__.py analysis/__init__.py
COPY analysis/l5/__init__.py analysis/l5/__init__.py
COPY analysis/l5/live_exec/spec.py analysis/l5/live_exec/spec.py
COPY analysis/l5/live_exec/ledger.py analysis/l5/live_exec/ledger.py
COPY analysis/l5/live_exec2/spec.py analysis/l5/live_exec2/spec.py
COPY analysis/l5/live_exec2/runner_spec.py analysis/l5/live_exec2/runner_spec.py
COPY analysis/l5/live_exec2/latch.py analysis/l5/live_exec2/latch.py
COPY analysis/l5/live_exec2/orders.py analysis/l5/live_exec2/orders.py
COPY analysis/l5/live_exec2/sides.py analysis/l5/live_exec2/sides.py
COPY analysis/l5/live_exec2/timing.py analysis/l5/live_exec2/timing.py
COPY analysis/l5/live_exec2/runner.py analysis/l5/live_exec2/runner.py
COPY analysis/l5/live_exec3/spec.py analysis/l5/live_exec3/spec.py
COPY analysis/l5/live_exec3/runner.py analysis/l5/live_exec3/runner.py
COPY analysis/l5/live_micro/spec.py analysis/l5/live_micro/spec.py
COPY analysis/l5/live_micro/ledger.py analysis/l5/live_micro/ledger.py

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_exec1xr_meta.py scripts/l5_exec1xr_meta.py
COPY scripts/l5_exec1xr2_run.py scripts/l5_exec1xr2_run.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.l5_exec1xr2_run"]
