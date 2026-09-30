# BTC15m PROFIT_LOCK_5 bounded capacity probe -- ONE market, then gone.
#
# PROPOSED, NOT BUILT, NOT DEPLOYED.
#
# THE COPY LIST IS THE RECOMPUTED AST IMPORT CLOSURE OF THE ENTRYPOINT, and nothing
# else. There are no manual additions: the previous revision listed six modules as
# REQUIRED-BY-DESIGN because the entrypoint did not import them, which concealed an
# unreachable component behind a COPY line. The entrypoint now imports the reviewed
# LOCK5 composition, so every required module appears below because it is REACHABLE.
# tests/test_lock5_entrypoint_composition.py recomputes this closure and fails if any
# of them stops being reachable.
#
# RAMP_LADDER_001 IS ABSENT FROM THE GRAPH, NOT JUST FROM THIS FILE.
#   2026-09-16: the R1 canary was authorized for ONE market at $0.05 and committed
#   $19.3295 to one of eleven, because pmus_l5_ramp_ladder.active() auto-loaded three
#   ten-day-old owner rungs and silently replaced the live limits -- per-market
#   $0.05 -> $7.50, daily stop -$1.00 -> -$15.00.
#   The translation composes the order body from src/l5_order_body.build directly --
#   which is exactly what pmus_micro_executor._body delegates to, proven byte-identical
#   by tests/test_r1_body_delegation_equivalence.py -- and the completion economics from
#   pmus_q1_executor, which has no ladder dependency. pmus_micro_executor is therefore
#   NOT in the closure and is NOT modified, so its other users are unaffected.
#   The LOCK5 probe has EXACTLY ONE risk/sizing authority: the reviewed
#   mandate/GuardedTransport boundary.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
# the only runtime dependencies, identical to the sibling probe images
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY scripts/l5_pmus_live_oneshot.py scripts/l5_pmus_live_oneshot.py
# READ-ONLY out-of-sample settlement sweep. GET only: it holds a
# ReadOnlyTransport, imports no engine, money path, mandate or
# authorization, and an AST test asserts no order-path name appears in it.
COPY scripts/pmus_oos_settlement_sweep.py scripts/pmus_oos_settlement_sweep.py
# READ-ONLY R5 persistence-signature extraction. Opens the journal on the
# volume and prints; it contains no writer and re-hashes the file before
# and after so `unmutated` is measured rather than claimed.
COPY scripts/r5_signature_extract.py scripts/r5_signature_extract.py
# PREVIEW-ONLY balance-admission probe. Calls /v1/order/preview and
# nothing else; an AST test asserts the order-creation endpoint appears
# in no string constant and that submit/cancel/arm are never named.
COPY scripts/pmus_balance_admission_probe.py scripts/pmus_balance_admission_probe.py
COPY src/l5_persistence_signature.py src/l5_persistence_signature.py
COPY scripts/pmus_completeness_rule.py scripts/pmus_completeness_rule.py
COPY scripts/audit_economic_complete.py scripts/audit_economic_complete.py
# Reachable from audit_economic_complete and MISSING until 2026-09-30: the
# closure guard walked only the main entrypoint, so every auxiliary script's
# own imports were invisible to it and this file would have ImportError'd in
# the image. The guard is now the union over all declared entrypoints.
COPY src/l5_replay_input.py src/l5_replay_input.py
COPY data/pmus_btc15m_tape_oos/oos_tape.jsonl data/pmus_btc15m_tape_oos/oos_tape.jsonl
COPY src/__init__.py src/__init__.py
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
COPY src/pmus_completion_shadow.py src/pmus_completion_shadow.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
COPY src/pmus_l5_action_shadow.py src/pmus_l5_action_shadow.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_l5_multi_shadow.py src/pmus_l5_multi_shadow.py
COPY src/pmus_l5_native_engine.py src/pmus_l5_native_engine.py
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_live_mandate.py src/pmus_live_mandate.py
COPY src/pmus_lock5_live_adapter.py src/pmus_lock5_live_adapter.py
COPY src/pmus_lock5_translation.py src/pmus_lock5_translation.py
COPY src/pmus_native_live_overlay.py src/pmus_native_live_overlay.py
COPY src/pmus_native_money_path.py src/pmus_native_money_path.py
COPY src/pmus_native_venue.py src/pmus_native_venue.py
COPY src/pmus_paired_log.py src/pmus_paired_log.py
COPY src/pmus_priority_queue.py src/pmus_priority_queue.py
COPY src/pmus_probe_observability.py src/pmus_probe_observability.py
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
# ---- the bounded q1 HOLD_OPEN_EXTEND canary and its decision feed.
# The canary makes OPEN/HOLD/COMPLEMENT/TAKE decisions from the AUTHENTICATED
# WS stream only; the public REST BBO is a ~30s cache (1.43-2.07 distinct book
# states/min against the WS's ~590, analysis/V1_PUBLIC_FEED_CEILING.md) and is
# not a basis for a queue-sensitive decision. pmus_ws_live_state reuses the
# recorder's own auth_headers/subscribe_frames/guard_outbound, so it cannot
# emit anything but a read-only subscription and holds no mutation path.
COPY src/pmus_q1_canary.py src/pmus_q1_canary.py
COPY src/pmus_ws_live_state.py src/pmus_ws_live_state.py
# the canonical market row, shared with pmus_micro_executor rather than
# duplicated beside it, and the venue's own series listing -- selection
# never manufactures a slug from the clock.
COPY src/pmus_market_row.py src/pmus_market_row.py
# the single order-state classifier, reused from pmus_q1_executor
COPY src/pmus_order_lifecycle.py src/pmus_order_lifecycle.py
COPY src/pmus_canonical_accounts.py src/pmus_canonical_accounts.py
COPY src/l5_mode_switch.py src/l5_mode_switch.py
COPY src/l5_decision_shadow.py src/l5_decision_shadow.py
COPY src/pmus_ws_lifecycle_observer.py src/pmus_ws_lifecycle_observer.py
COPY analysis/l5/l5_switch_model.json analysis/l5/l5_switch_model.json
COPY src/pmus_book_tape.py src/pmus_book_tape.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/action_model_v2.py src/polymarket_l5/action_model_v2.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py
# ---- authorization artefacts. HALT present means safe_shutdown before armed()
COPY analysis/l5/authorization/ analysis/l5/authorization/
# restartPolicyType NEVER is set on the SERVICE, not here: a one-shot the platform
# restarts is not a one-shot.
# The bounded probe starts the canary. restartPolicyType NEVER is set on the
# SERVICE: a one-shot the platform restarts is not a one-shot.
CMD ["python3", "-m", "src.pmus_q1_canary"]
