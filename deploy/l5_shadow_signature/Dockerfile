# NO-POST shadow and signature extraction.
#
# This image deliberately does NOT contain the money path, the mandate, the
# authorization ledger or the transport: the strongest no-POST guarantee
# available is an image in which the code to create an order does not exist,
# which is stronger than any runtime flag.
#
# THE COPY LIST IS MEASURED, NOT GUESSED. The first build of this image
# crashed with ImportError on l5_authenticated_observation because the list
# was written by hand. tests/test_shadow_image_closure.py imports the entry
# points, walks sys.modules for the real transitive closure, and fails if
# this file does not cover it -- or if it names a module the closure does not
# need.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

# THE FEED NEEDS THESE. The first fixed build reported
# SHADOW_FEED_NOT_READY with ModuleNotFoundError: websockets -- the
# authenticated subscription could never connect because the image had no
# dependencies at all. That is the real cause of every `ticks: 0` window;
# before the readiness check it was invisible. Same pins as the other
# PMUS images so the shadow observes through the same client.
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"

COPY src/__init__.py src/__init__.py
COPY src/l5_authenticated_observation.py src/l5_authenticated_observation.py
COPY src/l5_live_book_feed.py src/l5_live_book_feed.py
COPY src/l5_market_binding.py src/l5_market_binding.py
COPY src/l5_persistence_signature.py src/l5_persistence_signature.py
COPY src/l5_session_journal.py src/l5_session_journal.py
COPY src/l5_window_actions.py src/l5_window_actions.py
COPY src/pmus_completion_shadow.py src/pmus_completion_shadow.py
COPY src/pmus_l5_native_engine.py src/pmus_l5_native_engine.py
COPY src/pmus_native_parity.py src/pmus_native_parity.py

COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
COPY src/pmus_l5_action_shadow.py src/pmus_l5_action_shadow.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_l5_multi_shadow.py src/pmus_l5_multi_shadow.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_priority_queue.py src/pmus_priority_queue.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/action_model_v2.py src/polymarket_l5/action_model_v2.py

# DELIBERATELY NOT COPIED, and this is the no-POST guarantee:
#   src/pmus_q1_executor.py            -- the order path
#   src/polymarket_l5/transfer_backtest.py
# Both are reached only through the lazy accessors `_q1()` and `_tb()` in
# l5_venue_adapter, which that module documents as paths a read-only recorder
# image must never take. tests/test_shadow_image_packaging.py asserts they are
# absent from the image AND that the shadow's own code path never imports them,
# so the exclusion is proven rather than asserted.

COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_r7_shadow_runner.py scripts/l5_r7_shadow_runner.py
COPY scripts/r5_signature_extract.py scripts/r5_signature_extract.py

CMD ["python3", "-m", "scripts.l5_r7_shadow_runner"]
