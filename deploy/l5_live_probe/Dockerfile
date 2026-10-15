# ONE-SHOT BOUNDED LIVE PROBE.
#
# The only image in this repository that can send a real order, and it is a
# separate one-shot service precisely so that the 24/7 observer never
# becomes one. Nothing here is copied into deploy/pmus_ws_recorder, and
# nothing there calls arm().
#
# It carries l5_pmus_transport, whose arm() installs the only POST-capable
# client that exists -- and even that requires BOTH a passed preflight in
# this process AND the owner's session token. Until then the transport holds
# pmus_auth.ReadOnlyTransport, which raises on any method but GET, so a
# disarmed process does not merely decline to send an order: it holds no
# object able to send one.
#
# Deliberately NOT here:
#   pmus_q1_executor      place_limit_buy, cancel_all_deadman, run_campaign
#   pmus_micro_executor   a second order representation
#   pmus_migration_close  close-position paths
# This probe needs exactly one bounded order, not a campaign engine.
#
# It runs once and exits. The authorization it spends is written to the
# volume BEFORE arming, so a crash spends it too and the next window cannot
# inherit it. restartPolicyType is NEVER.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"

COPY src/__init__.py src/__init__.py
# signing and fees
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/pmus_fees.py src/pmus_fees.py
# the one shared order serializer, and the preview/echo gate
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
# the proven execution transport: preflight, arm, preview-then-submit, cancel
COPY src/l5_pmus_transport.py src/l5_pmus_transport.py
# inventory, exposure reservation, safety overlay, venue reconciliation
COPY src/l5_live_execution.py src/l5_live_execution.py
# the SECOND treatment. Reads the frozen rule's output and turns the actions
# it refused STRICT_NOT_EVALUABLE -- restable, but unprovable in replay --
# into genuine resting-order intents. It adds nothing to L5_NATIVE's decision.
COPY src/l5_maker_execution.py src/l5_maker_execution.py
COPY src/l5_probe_manifest.py src/l5_probe_manifest.py
COPY src/l5_pmus_live_wiring.py src/l5_pmus_live_wiring.py
# the frozen engine, byte-identical to 030d8bf. Pure decision code.
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY src/l5_fidelity_report.py src/l5_fidelity_report.py
# the AUTHENTICATED book. The probe subscribes to the same stream the
# recorder validates every gate against, for ONE market, in its own
# process -- it cannot see the recorder's memory from another service, and
# the REST book is served unsigned from the public gateway. This module is
# read-only: it carries the recorder's subscribe frames, its outbound frame
# guard and its event fold, and no order path.
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/l5_live_book_feed.py src/l5_live_book_feed.py
# observation: L5's public activity feed and window arithmetic
COPY src/l5_authenticated_observation.py src/l5_authenticated_observation.py
# the ONE implementation of the window arithmetic and the L5 action
# feed, shared with the continuous recorder. The probe's private copy
# of the action builder is what broke Probe 02.
# market IDENTITY. l5_window_actions imports this at module scope and
# admits nothing without it: an L5 action may enter a window only if its own
# CTF token is one of the two the venue publishes for that exact window.
# Probe 05 traded two actions from OTHER markets because the filter matched
# on timestamp alone.
COPY src/l5_market_binding.py src/l5_market_binding.py
COPY src/l5_window_actions.py src/l5_window_actions.py
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
# l5_pmus_realtime_observer imports these at MODULE scope, so they are
# required to import it at all -- and new_l5_actions imports the observer
# lazily, inside the observing loop, so a missing one does not fail at
# startup: it kills the run mid-window. That is exactly what happened to
# Probe 02 (armed, real money, silent) and to the 21:36Z observe-only run.
# The two functions actually used, action_key and l5_to_pmus_action, touch
# neither pv2 nor state_key -- these are import-time dependencies only, so
# the JSON model artifacts they load lazily are deliberately NOT packaged.
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
# evidence and the one-market authorization ledger
COPY src/l5_session_journal.py src/l5_session_journal.py
COPY src/l5_session_integrity.py src/l5_session_integrity.py
COPY src/l5_live_authorization.py src/l5_live_authorization.py
COPY src/l5_queue_measurement.py src/l5_queue_measurement.py
COPY src/l5_two_sided_probe.py src/l5_two_sided_probe.py

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_pmus_live_oneshot.py scripts/l5_pmus_live_oneshot.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "scripts.l5_pmus_live_oneshot"]
