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
# scripts/l5_e1_cohort_run.py, computed rather than guessed -- 38 first-party
# files (local_faults.py joined it with the error-taxonomy fix). A
# missing COPY is invisible until the container starts, and in this
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

# THE RUNTIME DEPENDENCY, AND THE DEFECT OF 2026-10-07 16:48:58Z.
#
# This image used to install NOTHING. `python:3.11-slim` ships only
# packaging/pip/setuptools/wheel, proven on the deployed container, and
# `cryptography` is the single third-party module E1 needs: every
# authenticated PMUS request is Ed25519-signed by `src.pmus_auth`.
#
# It went unnoticed because the import is LAZY -- it lives inside
# `signed_headers`, not at module scope -- so importing the entrypoint loads
# zero third-party modules and the build-time import assertion below passed
# while the image could not sign anything. The first authenticated request
# is the PREVIEW, immediately after WINDOW_OPENED, so the failure surfaced
# at the worst possible moment and wearing the wrong name: five
# `PreviewRejected` in 20.8 ms, recorded as VENUE_ERROR_STORM against a
# venue that had never been contacted.
#
# THE SAME DEFECT, A SECOND TIME, FOUND BY THE BBO PROBE.
# `src/l5_pmus_auth_book.py` does `import websockets` INSIDE `_run()` --
# again lazy, again invisible to the build-time import assertion below --
# and `websockets` was not in this image. The AuthBook thread therefore
# died on its first line the moment E1's decision feed was started:
#
#   Exception in thread auth-book: ModuleNotFoundError: No module named
#   'websockets'   (deployment cc131f76, 2026-10-07T22:19:53Z)
#
# E1 would have failed CLOSED -- no connected book, so health() refuses
# and no window ever opens -- but it could never have traded either, and
# the disarmed validation could not see it because a disarmed boot never
# starts the feed. Sibling images (pmus_ws_recorder, l5_window_collector,
# pmus_ws_book_diag) have always installed it; this one did not.
#
# PINNED to the versions the test suite actually runs against, so "green
# locally" and "works in the image" are claims about the same libraries.
RUN pip install --no-cache-dir cryptography==49.0.0 websockets==17.2

COPY src/__init__.py              src/__init__.py
COPY src/l5_order_body.py         src/l5_order_body.py
COPY src/l5_preview_gate.py       src/l5_preview_gate.py
COPY src/pmus_auth.py             src/pmus_auth.py
# PMUS-NATIVE MARKET IDENTITY AND DECISION FEED. `l5_pmus_transport`
# carries the proven BTC15m slug regex and `resolve_market`, which already
# knows that /v1/market/slug/ 404s for these windows while /v1/events/slug/
# serves them. `l5_pmus_auth_book` is the proven AUTHENTICATED book -- the
# only decision feed PMUS has -- and it is reused UNMODIFIED rather than
# re-implemented.
#
# ITS CLOSURE DRAGS IN pmus_q1_executor, DELIBERATELY ACCEPTED. That module
# can place and cancel orders, so a second order-capable stack is present
# in this image. Two things make it unreachable rather than merely unused:
# importing it performs no network, order, cancel or campaign action (tests
# prove it, statically and by import), and E1 REFUSES TO BOOT if any
# PMUS_Q1_* live/arming authorization is present in the environment
# (`arming.assert_no_foreign_executor_authorization`). E1 calls nothing in
# it; only E1's own named latch and order path is reachable.
COPY src/l5_pmus_transport.py     src/l5_pmus_transport.py
COPY src/l5_live_execution.py     src/l5_live_execution.py
COPY src/l5_faithful_translation.py  src/l5_faithful_translation.py
COPY src/l5_execution_translations.py  src/l5_execution_translations.py
COPY src/pmus_fee_schedule.py     src/pmus_fee_schedule.py
COPY src/pmus_fees.py             src/pmus_fees.py
COPY src/l5_pmus_auth_book.py  src/l5_pmus_auth_book.py
COPY src/l5_shadow_scoring.py  src/l5_shadow_scoring.py
COPY src/l5_venue_adapter.py  src/l5_venue_adapter.py
COPY src/pmus_l5_action_shadow.py  src/pmus_l5_action_shadow.py
COPY src/pmus_l5_fidelity_live.py  src/pmus_l5_fidelity_live.py
COPY src/pmus_l5_multi_shadow.py  src/pmus_l5_multi_shadow.py
COPY src/pmus_l5_persistence_v2.py  src/pmus_l5_persistence_v2.py
COPY src/pmus_priority_queue.py  src/pmus_priority_queue.py
COPY src/pmus_q1_executor.py  src/pmus_q1_executor.py
COPY src/pmus_sports_collector.py  src/pmus_sports_collector.py
COPY src/pmus_sports_l5_decision.py  src/pmus_sports_l5_decision.py
COPY src/pmus_status_report.py  src/pmus_status_report.py
COPY src/pmus_ws_evidence_recorder.py  src/pmus_ws_evidence_recorder.py
COPY src/polymarket_l5/__init__.py  src/polymarket_l5/__init__.py
COPY src/polymarket_l5/action_model.py  src/polymarket_l5/action_model.py
COPY src/polymarket_l5/action_model_v2.py  src/polymarket_l5/action_model_v2.py
COPY src/polymarket_l5/config.py  src/polymarket_l5/config.py
COPY src/polymarket_l5/transfer_backtest.py  src/polymarket_l5/transfer_backtest.py

COPY analysis/l5/e1_cohort/       analysis/l5/e1_cohort/
COPY analysis/l5/live_exec/       analysis/l5/live_exec/
COPY analysis/l5/live_exec2/      analysis/l5/live_exec2/
COPY analysis/l5/live_exec3/spec.py  analysis/l5/live_exec3/spec.py
COPY analysis/l5/live_exec5/spec.py  analysis/l5/live_exec5/spec.py
COPY analysis/l5/live_micro/      analysis/l5/live_micro/
COPY analysis/l5/measurement/     analysis/l5/measurement/

COPY scripts/l5_e1_cohort_run.py  scripts/l5_e1_cohort_run.py
# READ-ONLY live readiness proof. Not the entrypoint, so it is outside the
# closure below. It imports no latch, order body or preview gate and
# checks sys.modules to prove it. It DOES use credentials, because the
# only PMUS decision feed is an authenticated socket -- signing is not
# ordering, and the script says so rather than implying otherwise.
COPY scripts/l5_e1_ws_readiness_check.py  scripts/l5_e1_ws_readiness_check.py
# PHASE 1 PMUS IDENTITY PROOF. Read-only GETs plus exactly one
# /v1/order/preview; refuses POST /v1/orders structurally and proves via
# sys.modules that no Polymarket.com venue module was loaded.
COPY scripts/l5_e1_pmus_identity_proof.py  scripts/l5_e1_pmus_identity_proof.py
# BBO SEMANTIC PROBE. Read-only: three independent readings of the same
# slug (REST /bbo, REST /book, the authenticated AuthBook feed) compared
# against each other and against the proven lineage's reader. It replaces
# the E1 latch's consume functions with raises before doing any work, and
# proves via sys.modules that no order-capable module was loaded.
COPY scripts/l5_e1_bbo_semantics_probe.py  scripts/l5_e1_bbo_semantics_probe.py
# READ BACK the captures from the volume. Opens files and prints; no
# network, no credential, no transport, no order module, proven by a
# sys.modules check after the work.
COPY scripts/l5_e1_bbo_recover.py  scripts/l5_e1_bbo_recover.py
# THE TWO-SIDED BBO PROOF. Read-only. Waits for a live state with BOTH
# an executable bid and ask, then adjudicates FOUR readings of the same
# slug at one instant: REST /bbo, REST /book top levels, the RAW
# authenticated WS frame (captured by wrapping AuthBook.ingest from the
# outside, so it is the venue's own bytes and not a restatement of the
# fold), and the AuthBook snapshot. On the FIRST two-sided state found it
# reports agreement or DISAGREEMENT and stops -- it does not hunt for a
# result.
COPY scripts/l5_e1_bbo_twosided_proof.py  scripts/l5_e1_bbo_twosided_proof.py

# READ-ONLY window-identity probe. Builds only pmus_auth.ReadOnlyTransport,
# which raises on any method but GET, so it cannot construct an order.
COPY scripts/l5_e1_window_identity_probe.py \
     scripts/l5_e1_window_identity_probe.py

# READ-ONLY BTC15m availability diagnosis. ReadOnlyTransport only.
COPY scripts/l5_pmus_btc15m_availability.py \
     scripts/l5_pmus_btc15m_availability.py

# READ-ONLY BTC15m tradeability watcher. ReadOnlyTransport + AuthBook read.
COPY scripts/l5_pmus_btc15m_watcher.py \
     scripts/l5_pmus_btc15m_watcher.py

# READ-ONLY A6 settlement close-out + 15m halt bracket. ReadOnlyTransport.
COPY scripts/l5_a6_settle_and_halt_bracket.py \
     scripts/l5_a6_settle_and_halt_bracket.py

# CANCEL-AND-VERIFY ONLY. Approved deadman; builds no order body.
COPY scripts/l5_e1_cancel_outstanding.py \
     scripts/l5_e1_cancel_outstanding.py

# READ-ONLY verification of the repaired outstanding-order invariant against
# real venue state. GETs only; imports nothing that can place an order.
COPY scripts/l5_e1_order_state_verify.py \
     scripts/l5_e1_order_state_verify.py

# READ-ONLY financial reconciliation of the 1215z and 1230z windows. Runs
# here because this is the service with the ledger volume mounted. GETs the
# venue, reads the append-only ledgers, writes nothing.
COPY scripts/l5_e1_financial_reconcile.py \
     scripts/l5_e1_financial_reconcile.py

# E1-ATTRIBUTED cashflow, matched by order id on a SHARED account. GETs only.
COPY scripts/l5_e1_attributed_cashflow.py \
     scripts/l5_e1_attributed_cashflow.py

# PRE-LIVE VALIDATION: the repaired reader, the tape-restored registry, a
# live-book shadow window and the contamination control. GETs only, arms
# nothing, submits nothing.
COPY scripts/l5_e1_prelive_validation.py \
     scripts/l5_e1_prelive_validation.py

# THE OPERATOR'S EXACT-ID EMERGENCY CANCELLATION TOOL.
#
# If this process dies with an order resting, its own handover deadman never
# runs. The only prior operator tool cancelled by MARKET, which on this
# SHARED account would cancel another desk's orders in the same window --
# the mechanism that got fingerprint 0c12d361 superseded out of the engine.
# This one cancels only ids named AND present in E1's own durable tape, one
# POST /v1/order/{id}/cancel each, through the frozen `cancel_exact`, and it
# dry-runs unless the operator sets E1_CANCEL_EXECUTE=YES.
COPY scripts/l5_e1_cancel_by_id.py \
     scripts/l5_e1_cancel_by_id.py

# THE BINDING RETIREMENT TOOL. A second authorization cannot bind a window
# while the completed probe's immutable binding names the first one -- the
# three-attempt probe refused itself with PROBE_IS_BOUND_TO_WINDOW_3 having
# placed nothing. This tool verifies the previous probe is complete at the
# venue and in the ledger, then COPIES its binding into an O_EXCL archive;
# it releases nothing, deletes nothing, and the new probe binds in a new
# epoch namespace. It was missing from this COPY list on its first deploy,
# which is why the build-time import proof below now names it too.
COPY scripts/l5_e1_retire_binding.py \
     scripts/l5_e1_retire_binding.py

RUN touch scripts/__init__.py analysis/__init__.py analysis/l5/__init__.py \
          analysis/l5/live_exec3/__init__.py analysis/l5/live_exec5/__init__.py

ENV PYTHONUNBUFFERED=1

# BUILD-TIME PROOF that the COPY list is complete and the frozen spec loads.
# If a module is missing or the fingerprint literal does not match its own
# content, the BUILD fails here rather than the container failing at 02:00
# with a window at stake.
RUN python3 -c "import scripts.l5_e1_cohort_run as m; \
import scripts.l5_e1_retire_binding as r; \
import scripts.l5_e1_cancel_by_id as c; \
import scripts.l5_e1_prelive_validation as v; \
from analysis.l5.e1_cohort.spec import E1_FINGERPRINT, assert_frozen; \
assert_frozen(); print('BUILD OK fingerprint', E1_FINGERPRINT)"

# BUILD-TIME PROOF THAT THE LAZY IMPORTS RESOLVE.
#
# Neither `cryptography` nor `websockets` is imported at module scope by
# anything E1's entrypoint loads, so the import assertion below passes
# with both absent. Both have now shipped missing, once each. So the
# build imports them BY NAME, exactly as the lazy call sites do.
RUN python3 -c "import websockets, cryptography; \
print('BUILD OK lazy imports', websockets.__version__, \
cryptography.__version__)"

# BUILD-TIME PROOF THAT THE SECOND ORDER-CAPABLE STACK IS INERT.
#
# `src/pmus_q1_executor.py` is in this image because it is in AuthBook's
# transitive closure, and it can place and cancel orders from its own
# PMUS_Q1_* namespace. Two facts make it unreachable, and both are proven
# HERE rather than asserted in a comment:
#
#   1. importing E1's entrypoint does not load it at all (pmus_book
#      imports AuthBook lazily, inside start());
#   2. E1 REFUSES TO BOOT if any Q1 live/arming authorization is present.
#
# If either stops being true, the BUILD fails.
RUN python3 -c "import sys; import scripts.l5_e1_cohort_run; \
assert 'src.pmus_q1_executor' not in sys.modules, 'Q1 EXECUTOR LOADED BY E1'; \
import os; from analysis.l5.e1_cohort import arming as A; \
os.environ['PMUS_Q1_LIVE'] = 'YES'; \
r = 0; \
exec('try:\n A.assert_no_foreign_executor_authorization()\nexcept SystemExit:\n r = 1'); \
assert r == 1, 'E1 DID NOT REFUSE TO BOOT BESIDE AN ARMED Q1'; \
print('BUILD OK q1 inert and boot-refused')"

# BUILD-TIME PROOF THAT THIS IMAGE CAN SIGN. The assertion above cannot
# establish it -- a lazy import is invisible to it -- which is exactly how
# the missing dependency reached production. So sign something here, with a
# THROWAWAY generated key and no credential present, and fail the BUILD if
# the signing stack is absent or unusable.
RUN python3 -c "\
from cryptography.hazmat.primitives.asymmetric import ed25519; \
import base64, cryptography; \
k = ed25519.Ed25519PrivateKey.generate(); \
msg = b'1700000000GET/v1/_e1_local_signing_selftest'; \
sig = base64.b64encode(k.sign(msg)); \
assert len(base64.b64decode(sig)) == 64, 'Ed25519 signature is not 64 bytes'; \
print('BUILD OK signing cryptography', cryptography.__version__)"

CMD ["python3", "-m", "scripts.l5_e1_cohort_run"]
