# E1 MECHANICS COHORT. ORDER-CAPABLE. DEPLOYED DISARMED.
#
# This image CAN reach PMUS and CAN place orders -- that is its purpose, and
# claiming otherwise would be a false safety claim. What stops it is its own
# refusal: E1 reads PMUS_E1_LIVE / _SESSION_ID / _ARM / _GATE_EVIDENCE and
# pins PMUS_E1_AUTHORIZED_FINGERPRINT. With those absent it boots, asserts the
# frozen spec, prints the preflight and the DISARMED refusal, and returns 0
# WITHOUT requesting a snapshot, claiming a latch slot or reaching an order
# endpoint.
#
# E1 does NOT read PMUS_EXEC1_* -- those are XR2W's keys and this service
# already carries them. Verified: with all five set, arming.armed() is False.
#
# E1 state lives at /state/pmus-ws/e1_cohort. XR2W's SPENT A6 latch at
# /state/pmus-ws/exec1x is NOT touched; it is evidence.
#
# The COPY list below is the TRANSITIVE AST IMPORT CLOSURE of
# scripts/l5_e1_cohort_run.py, computed rather than guessed -- 37 first-party
# files. A missing COPY is invisible until the container starts, and in this
# repository that has already cost a failed deploy three times (most recently
# src/l5_market_binding.py, which entered the closure when authorization.py
# began importing selector.py AFTER the list was computed), so the final RUN
# asserts the entrypoint actually imports.
#
# A hand-maintained list drifts the moment an import is added. The closure is
# therefore recomputed and compared against this file by
# analysis/l5/e1_cohort/tests/test_dockerfile_closure.py, so an import added
# without a COPY fails the SUITE, not the deploy.
FROM python:3.11-slim
WORKDIR /app

COPY src/__init__.py              src/__init__.py
COPY src/l5_book_shadow.py        src/l5_book_shadow.py
COPY src/l5_clob_ws.py            src/l5_clob_ws.py
COPY src/l5_market_binding.py     src/l5_market_binding.py
COPY src/l5_order_body.py         src/l5_order_body.py
COPY src/l5_preview_gate.py       src/l5_preview_gate.py
COPY src/pmus_auth.py             src/pmus_auth.py
COPY src/pmus_fee_schedule.py     src/pmus_fee_schedule.py
COPY src/pmus_fees.py             src/pmus_fees.py

COPY analysis/l5/e1_cohort/       analysis/l5/e1_cohort/
COPY analysis/l5/live_exec/       analysis/l5/live_exec/
COPY analysis/l5/live_exec2/      analysis/l5/live_exec2/
COPY analysis/l5/live_exec3/spec.py  analysis/l5/live_exec3/spec.py
COPY analysis/l5/live_exec5/spec.py  analysis/l5/live_exec5/spec.py
COPY analysis/l5/live_micro/      analysis/l5/live_micro/
COPY analysis/l5/measurement/     analysis/l5/measurement/

COPY scripts/l5_e1_cohort_run.py  scripts/l5_e1_cohort_run.py
# READ-ONLY live readiness proof. Not the entrypoint, so it is outside the
# closure below; it imports no transport, latch, order body or credential
# module and checks sys.modules to prove it.
COPY scripts/l5_e1_ws_readiness_check.py  scripts/l5_e1_ws_readiness_check.py

RUN touch scripts/__init__.py analysis/__init__.py analysis/l5/__init__.py \
          analysis/l5/live_exec3/__init__.py analysis/l5/live_exec5/__init__.py

ENV PYTHONUNBUFFERED=1

# BUILD-TIME PROOF that the COPY list is complete and the frozen spec loads.
# If a module is missing or the fingerprint literal does not match its own
# content, the BUILD fails here rather than the container failing at 02:00
# with a window at stake.
RUN python3 -c "import scripts.l5_e1_cohort_run as m; \
from analysis.l5.e1_cohort.spec import E1_FINGERPRINT, assert_frozen; \
assert_frozen(); print('BUILD OK fingerprint', E1_FINGERPRINT)"

CMD ["python3", "-m", "scripts.l5_e1_cohort_run"]
