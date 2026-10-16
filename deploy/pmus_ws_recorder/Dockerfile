# Authenticated READ-ONLY Polymarket US markets-stream recorder.
# Subscribes to wss://api.polymarket.us/v1/ws/markets (book + trade tape)
# and appends verbatim JSONL under /state/pmus-ws. The only outbound
# frames are subscribe/unsubscribe/heartbeat (guard-enforced + tested);
# no order endpoint or private stream exists in this worker.
#
# DEPLOYMENT ISOLATION (issue #167 comment 5521210994): Railway's legacy
# railway.json Config-as-Code is deprecated. The supported permanent pin
# is the per-service variable
#     RAILWAY_DOCKERFILE_PATH=/deploy/pmus_ws_recorder/Dockerfile
# (collector service pins its own Dockerfile the same way). As a
# repo-side tripwire this image BAKES its role below: if a branch push
# ever cross-builds services again, the wrong worker exits loudly with
# SERVICE_ROLE_MISMATCH at startup instead of running silently.
# See deploy/RAILWAY_ISOLATION.md for the acceptance checklist.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
COPY src/pmus_ws_evidence_recorder_enriched.py src/pmus_ws_evidence_recorder_enriched.py
COPY src/pmus_spring_shadow_replay.py src/pmus_spring_shadow_replay.py
COPY src/pmus_l5_compute_allocator.py src/pmus_l5_compute_allocator.py
COPY src/pmus_sports_collector.py src/pmus_sports_collector.py
COPY src/pmus_status_report.py src/pmus_status_report.py
COPY src/pmus_priority_queue.py src/pmus_priority_queue.py
# L5 action-shadow emission (per-market l5_shadow on every status row)
COPY src/pmus_l5_action_shadow.py src/pmus_l5_action_shadow.py
# read-only multi-market L5 shadow: canonical lookup v3 shared with the
# live probe and the Gemini shadow; no executor / order path is copied
COPY src/pmus_l5_multi_shadow.py src/pmus_l5_multi_shadow.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
# the continuous shadow policy evaluator and the probe module it reads its
# fee model and decision predicates from. This Dockerfile copies file by
# file, so a module the recorder imports but nobody COPYs is an ImportError
# in the image and a feature that is silently inert -- which is exactly how
# the first shadow-policy deploy came up doing nothing. test_pmus_ws_recorder_
# image_covers_its_import_closure now fails before that can happen again.
COPY src/l5_shadow_policy_eval.py src/l5_shadow_policy_eval.py
COPY src/l5_two_sided_probe.py src/l5_two_sided_probe.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY analysis/l5/l5_fidelity_pass.json analysis/l5/l5_fidelity_pass.json
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
# the canonical planner: spring_watch rows now carry probe_entry_action,
# computed with the SHARED planner so the recorder cannot drift from the
# decision layer (OWNER 5551236878)
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY analysis/l5/l5_action_model.json analysis/l5/l5_action_model.json
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
# READ-ONLY diagnostics are packaged for explicit operator/one-shot use only.
# They are deliberately NOT in ENTRYPOINT/CMD so a historical scan can never
# block continuous authenticated capture during a live slate.
COPY scripts/priority_vs_rotation_eval.py scripts/priority_vs_rotation_eval.py
COPY scripts/spring_candidate_tape_read.py scripts/spring_candidate_tape_read.py
COPY scripts/pmus_candidate_tape_readonly.py scripts/pmus_candidate_tape_readonly.py
# Authenticated identity/market preflight. It runs WHERE THE CREDENTIALS
# ALREADY LIVE so they are never copied to another service, and it keeps
# this image's promise that no order endpoint exists in it: it imports
# src/pmus_auth.py, which carries the Ed25519 signing and NOTHING that can
# place, amend or cancel an order. pmus_q1_executor is deliberately NOT
# copied here -- it holds place_limit_buy/cancel_all_deadman/run_campaign.
COPY scripts/pmus_auth_preflight_readonly.py scripts/pmus_auth_preflight_readonly.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/pmus_fees.py src/pmus_fees.py
COPY scripts/l5_raw_tape_observation.py scripts/l5_raw_tape_observation.py
COPY src/l5_authenticated_observation.py src/l5_authenticated_observation.py
# the ONE implementation of the window arithmetic and the L5 action
# feed. It was this file's private code; the bounded probe had a copy
# of it, and the copy is what broke Probe 02. Pure observation: no
# transport, no endpoint, no credential, no order path.
COPY src/l5_window_actions.py src/l5_window_actions.py
# The DISARMED end-to-end probe dry run. It runs WHERE THE CREDENTIALS LIVE
# because that is the only place the authenticated book and the account are
# both reachable. pmus_q1_executor is still NOT copied here: the transport
# no longer imports it, and the probe needs one bounded order rather than a
# campaign engine.
COPY scripts/l5_pmus_probe_dryrun.py scripts/l5_pmus_probe_dryrun.py
COPY scripts/l5_pmus_realtime_probe.py scripts/l5_pmus_realtime_probe.py
COPY src/l5_session_journal.py src/l5_session_journal.py
COPY src/l5_session_integrity.py src/l5_session_integrity.py
# copied so the integrity halt can verify ALL THREE frozen files at
# runtime. It is not otherwise used by the live path; verifying only
# the files that happen to be present would let a missing one pass.
COPY src/l5_fidelity_report.py src/l5_fidelity_report.py
COPY src/l5_pmus_live_wiring.py src/l5_pmus_live_wiring.py
COPY src/l5_probe_manifest.py src/l5_probe_manifest.py
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY src/l5_live_execution.py src/l5_live_execution.py
COPY src/l5_pmus_transport.py src/l5_pmus_transport.py
# The order serializer and the preview gate. Both are pure: no transport, no
# endpoint, no credential, and no import of any executor. The preflight needs
# the contract data in l5_order_body, and submit() needs the gate if this
# service is ever armed. Neither adds a path that can send an order --
# l5_preview_session, which CAN post a preview, stays out of this image.
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
COPY src/l5_pmus_realtime_observer.py src/l5_pmus_realtime_observer.py
COPY src/pmus_interaction_classifier.py src/pmus_interaction_classifier.py
RUN touch src/__init__.py

ENV PMUS_SERVICE_ROLE=ws-recorder \
    PMUS_WS_ROOT=/state/pmus-ws \
    PMUS_WS_LEAGUES=all \
    PMUS_WS_MAX_MARKETS=64 \
    PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.pmus_ws_evidence_recorder_enriched"]
