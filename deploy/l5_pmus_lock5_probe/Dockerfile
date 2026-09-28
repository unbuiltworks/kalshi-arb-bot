# BTC15m PROFIT_LOCK_5 bounded capacity probe -- ONE market, then gone.
#
# PROPOSED, NOT DEPLOYED. Written for review at candidate artifact 5deaf006.
#
# THE COPY LIST IS THE AST IMPORT CLOSURE OF THE ENTRYPOINT, computed rather than
# curated, so a lazy import cannot be missing at runtime. Nothing unrelated is
# added: no extra dependency, no extra runtime component.
#
# TWO REVIEW FINDINGS ARE ENCODED HERE. Read them before treating this as
# buildable; both are in analysis/pmus/PROBE_ARTIFACT_REVIEW_5deaf006.md.
#
# FINDING 1 -- THE WIRING IS NOT REACHABLE FROM THE ENTRYPOINT.
#   The closure of scripts/l5_pmus_live_oneshot.py is 38 files and contains
#   NEITHER pmus_lock5_translation NOR pmus_live_mandate NOR
#   pmus_probe_observability NOR pmus_completion_shadow. The one-shot does not
#   import the translation. An image built from the entrypoint closure alone
#   would therefore contain no LOCK5, no mandate boundary and no taker
#   completion. The six files below marked REQUIRED-BY-DESIGN are listed
#   explicitly for that reason; they are NOT yet pulled in by an import, and
#   making them reachable is an execution-class change that needs its own review.
#
# FINDING 2 -- THE RETIRED RAMP LADDER IS IN THE TRANSITIVE CLOSURE.
#   src/pmus_micro_executor.py defines per_market_first_leg_cap(), which lazily
#   imports src/pmus_l5_ramp_ladder and reads ladder.active()["per_market_cap_usd"].
#   That is the mechanism on the DO-NOT-USE-FOR-LIVE-AUTHORIZATION list
#   (RAMP_LADDER_001): persisted historical owner rungs auto-loaded and silently
#   replaced live limits, $0.05 -> $7.50, on 2026-09-16. It is DELIBERATELY NOT
#   COPIED below, so the lazy import would raise rather than resolve a stale rung
#   -- fail closed, not fail quiet. src/pmus_q1_executor_v4.py is copied because
#   pmus_micro_executor imports it at module scope and the body builder needs it.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
# the only runtime dependencies, unchanged from the sibling probe images
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/__init__.py src/__init__.py
# ---- AST import closure of scripts/l5_pmus_live_oneshot.py (38 files)
COPY scripts/l5_pmus_live_oneshot.py scripts/l5_pmus_live_oneshot.py
COPY src/l5_authenticated_observation.py src/l5_authenticated_observation.py
COPY src/l5_evidence_freeze.py src/l5_evidence_freeze.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_live_authorization.py src/l5_live_authorization.py
COPY src/l5_live_book_feed.py src/l5_live_book_feed.py
COPY src/l5_live_execution.py src/l5_live_execution.py
COPY src/l5_maker_execution.py src/l5_maker_execution.py
COPY src/l5_market_binding.py src/l5_market_binding.py
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_pmus_live_wiring.py src/l5_pmus_live_wiring.py
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/l5_pmus_transport.py src/l5_pmus_transport.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
COPY src/l5_probe_manifest.py src/l5_probe_manifest.py
COPY src/l5_session_journal.py src/l5_session_journal.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/l5_window_actions.py src/l5_window_actions.py
COPY src/pmus_arm_retry.py src/pmus_arm_retry.py
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
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py
# ---- REQUIRED-BY-DESIGN, not yet reachable by import (Finding 1)
COPY src/pmus_completion_shadow.py src/pmus_completion_shadow.py
COPY src/pmus_live_mandate.py src/pmus_live_mandate.py
COPY src/pmus_lock5_translation.py src/pmus_lock5_translation.py
COPY src/pmus_micro_executor.py src/pmus_micro_executor.py
COPY src/pmus_probe_observability.py src/pmus_probe_observability.py
COPY src/pmus_q1_executor_v4.py src/pmus_q1_executor_v4.py
# ---- authorization artefacts. HALT present means safe_shutdown before armed()
COPY analysis/l5/authorization/ analysis/l5/authorization/
# restartPolicyType NEVER is set on the SERVICE, not here: a one-shot that the
# platform restarts is not a one-shot.
CMD ["python3", "-m", "scripts.l5_pmus_live_oneshot"]
