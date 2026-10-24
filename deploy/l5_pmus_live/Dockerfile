# THE PRODUCTION PATH for the canonical L5 executor on Polymarket US BTC15m.
#
#   L5 realtime activity -> AUTHENTICATED PMUS BTC15m book (markets WS)
#     -> L5_NATIVE -> PMUS_TRANSLATION (both frozen at 030d8bf)
#     -> SAFETY_OVERLAY -> PmusTransport
#
# This is the process that places orders once armed, running identically
# before and after. It is NOT a shadow.
#
# PROVENANCE. The harness (runner, authenticated book) comes from the P39
# line; the execution core (l5_live_execution, l5_pmus_transport) is the
# canonical post-Probe-08 code. The two lines had diverged at c668665, and
# the deployed P39 image ran an execution core that predates every Probe
# 04-08 fix -- including the venue-order-price parser whose absence CRASHED
# Probe 08 the first time an order rested. That core is not deployed here.
#
# BASELINE, not a treatment. maker_treatment and maker_only are the wiring
# Probes 04-08 actually traded through. suppress_imbalance_increase is OFF:
# Probe 08's treatment was not established as an improvement, and the
# rejected Probe 09 prospective-inventory rule is absent from the image --
# no module reachable from the entrypoint imports it.
#
# STARTS DISARMED AND STAYS DISARMED WITHOUT THE OWNER. PmusTransport refuses
# every submit and cancel until BOTH (a) the full read-only preflight PASSED
# in this process for the current window's market, and (b) the owner's arm
# token is present and matches the session id (PMUS_Q1_LIVE=YES,
# PMUS_Q1_SESSION_ID, PMUS_Q1_ARM == PMUS_Q1_SESSION_ID). This image sets
# none of those. Each refused intent is recorded as NOT_ARMED.
#
# Kill switch: set L5_LIVE_HALT to any value -- deadman now, never arm.
#
# LIMITS (account protection only): $5 per market, $5 total exposure, 10
# shares max residual, 1 market, 8 live orders, $5 realized-loss halt.
#
# The COPY list below is the import closure walked from the entrypoint with
# ast, unioned with the P39 list so a lazily-imported module cannot be
# missing at runtime. tests/test_l5_live_runner_graft.py asserts it stays
# a superset of the closure.
#
# DEPLOYMENT ISOLATION (deploy/RAILWAY_ISOLATION.md): pin
#     RAILWAY_DOCKERFILE_PATH=/deploy/l5_pmus_live/Dockerfile
# on the service; the role is BAKED below and asserted at startup.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"

COPY src/__init__.py src/__init__.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_live_execution.py src/l5_live_execution.py
# The shared arming boundary. The runner now imports it at module scope, so
# an image without it cannot boot -- the same incomplete-closure defect that
# bit the preflight and live-probe images. test_the_image_copies_every_module
# _the_entrypoint_can_reach caught this before it ever ran.
COPY src/l5_live_authorization.py src/l5_live_authorization.py
# the bounded-campaign approval layer the runner now reads on every arm
# attempt. Missing from the image, the runner fails to import.
COPY src/l5_campaign_approval.py src/l5_campaign_approval.py
COPY src/l5_campaign_budget.py src/l5_campaign_budget.py
COPY src/l5_window_accounting.py src/l5_window_accounting.py
# The effective-runtime-configuration verifier. Shipped because the
# only configuration that matters is the one the RUNNING process has,
# so it has to be runnable in here:
#   python3 -m src.l5_runtime_config_verify
# It reads config and prints a verdict. It imports no transport, has
# no order path, and cannot arm, submit or cancel -- asserted by
# test_nothing_in_the_verifier_can_submit_arm_or_reach_a_venue.
COPY src/l5_runtime_config_verify.py src/l5_runtime_config_verify.py
COPY src/l5_queue_measurement.py src/l5_queue_measurement.py
COPY src/l5_defensive_completion.py src/l5_defensive_completion.py
COPY src/l5_two_sided_probe.py src/l5_two_sided_probe.py
COPY src/l5_maker_fill_model.py src/l5_maker_fill_model.py
COPY src/l5_two_sided_shadow.py src/l5_two_sided_shadow.py
# Disarmed-shadow validation additions. This image COPYs an explicit file
# list, so a module missing here is an ImportError at runtime, which would
# leave the protection ABSENT while the operator believed it was on.
# l5_authoritative_ledger_repair is deliberately NOT here: nothing in the
# image imports it (the repair entries are declarative data in the runner),
# and the graft test correctly refuses a module outside the closure.
COPY src/l5_halt_gate.py src/l5_halt_gate.py
# The read-only ownership registry. The runner imports it at boot to publish,
# over GET, which venue order ids are its own -- without that the watchdog
# cannot tell our resting order from a manual one on this SHARED account and
# must refuse to cancel anything. Caught missing by
# test_the_image_copies_every_module_the_entrypoint_can_reach, which is the
# test that exists because an import the image lacks fails only in production.
COPY src/l5_order_registry_server.py src/l5_order_registry_server.py
COPY src/l5_live_validation_gates.py src/l5_live_validation_gates.py
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_pmus_auth_book.py src/l5_pmus_auth_book.py
# lazily imported by the runner behind L5_COST_BASIS_AUDIT=YES. A LAZY import
# is exactly what the closure test could not see: the module was missing from
# this image and the audit raised ImportError at 01:46:08Z while looking
# wired. It is read-only and writes nothing.
COPY src/l5_cost_basis_audit.py src/l5_cost_basis_audit.py
COPY src/l5_depth_comparison.py src/l5_depth_comparison.py
# the bounded cancellation-mechanism leg. Imported EAGERLY by the
# runner, so leaving it out of this list crashloops the executor on
# boot rather than failing later -- and the closure test that exists
# to catch exactly that is already red on unrelated config files, so
# it could not have caught it. The mode is off unless
# L5_CANCEL_MECHANISM=YES, so shipping it changes nothing by itself.
COPY src/l5_cancel_mechanism.py src/l5_cancel_mechanism.py
# The INDEPENDENT inventory-driven shadow decision source, imported eagerly
# by the runner. It holds no transport and defines no order path, so shipping
# it cannot place an order; leaving it out would crashloop the executor on
# boot, which is why it is listed rather than left to the closure test.
# The shared intent-level safety boundary (Phase B). Imported eagerly by
# the runner, so the image needs it to boot at all.
#
# IT CHANGES THE MONEY PATH. `Runner.handle`'s three refusals -- window,
# sell scope, operator halt -- are now DECIDED by this module instead of
# inline, in the same order and with the same layer and reason strings
# (tests/test_l5_intent_gate.py pins both). Shipping it is therefore a
# money-path promotion and needs its own approval, separate from any
# shadow-only deployment. It adds no capability: a controller-generated
# intent is refused at EXECUTOR_BOUNDARY and is never given an L5 row.
COPY src/l5_intent_gate.py src/l5_intent_gate.py
# Order-ID-linked calibration capture. Observational: holds no transport,
# no credentials and no executor, and cannot submit, cancel or amend.
COPY src/l5_fill_calibration.py src/l5_fill_calibration.py
COPY src/l5_shadow_controller.py src/l5_shadow_controller.py
COPY src/l5_pmus_live_runner.py src/l5_pmus_live_runner.py
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/l5_pmus_transport.py src/l5_pmus_transport.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
COPY src/pmus_l5_action_shadow.py src/pmus_l5_action_shadow.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_l5_multi_shadow.py src/pmus_l5_multi_shadow.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_priority_queue.py src/pmus_priority_queue.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/action_model_v2.py src/polymarket_l5/action_model_v2.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py

# The cost-basis apply reads its inspected batch manifest from this path.
# Without it the apply aborts BATCH_MANIFEST_MISSING (safe, but it writes
# nothing). This image COPYs per file, so the manifest must be named.
# THE FROZEN v2 ACTION MODEL, which the shadow controller loads at
# construction. A DATA dependency the ast closure test cannot see: without
# this file `load_v2` raises, the controller self-disables with
# L5_SHADOW_DISABLED, and the service would look healthy while emitting no
# decisions at all -- the whole deliverable, absent and quiet.
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json

COPY config/cost_basis_batch_manifest_20261016.json config/cost_basis_batch_manifest_20261016.json
COPY config/cost_basis_batch_manifest_20261016b.json config/cost_basis_batch_manifest_20261016b.json
COPY config/cost_basis_batch_manifest_20261016c.json config/cost_basis_batch_manifest_20261016c.json
COPY config/cost_basis_batch_manifest_20261016d.json config/cost_basis_batch_manifest_20261016d.json

ENV PMUS_SERVICE_ROLE=l5-pmus-live \
    L5_LIVE_ROOT=/state/l5-live \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.l5_pmus_live_runner"]
