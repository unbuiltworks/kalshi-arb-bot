# L5 joined BTC15m window collector -- READ ONLY.
#
# A separate service from the authenticated recorder on purpose. The recorder
# is the one asset that cannot be rebuilt from anything else, and a collector
# that must run for days should not share a process with it.
#
# This image ships no order path of its own. `pmus_q1_executor` arrives as a
# transitive dependency of the recorder's websocket primitives, which this
# reuses rather than reimplements; the guarantee is not that the module is
# absent but that this service sets no arming variable, constructs no armed
# transport, and calls no mutation verb -- each asserted on the parse tree in
# tests/test_l5_window_collector.py.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42" "requests>=2"

COPY src/__init__.py src/__init__.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_priority_queue.py src/pmus_priority_queue.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_l5_action_shadow.py src/pmus_l5_action_shadow.py
COPY src/pmus_l5_multi_shadow.py src/pmus_l5_multi_shadow.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
COPY src/l5_authenticated_observation.py src/l5_authenticated_observation.py
COPY src/l5_market_binding.py src/l5_market_binding.py
COPY src/l5_window_actions.py src/l5_window_actions.py
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/l5_window_collector.py src/l5_window_collector.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/action_model_v2.py src/polymarket_l5/action_model_v2.py
COPY analysis/l5/l5_action_model.json analysis/l5/l5_action_model.json
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
COPY scripts/__init__.py scripts/__init__.py
COPY scripts/l5_window_collector_run.py scripts/l5_window_collector_run.py

RUN touch src/__init__.py scripts/__init__.py

ENV PMUS_SERVICE_ROLE=l5-window-collector \
    L5_COLLECTOR_ROOT=/state/l5-collector \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "scripts.l5_window_collector_run"]
