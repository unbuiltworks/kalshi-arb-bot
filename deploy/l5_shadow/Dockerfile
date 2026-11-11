# L5 SHADOW LOOP -- the permanent, isolated 24/7 research image
# (issue #167 comment 5574408755 item 5).
#
# READ-ONLY. Public keyless endpoints only (the existing collector's L5
# activity source); no credentials of any kind, no order path, no PMUS
# executor, no WebSocket recorder, no shared volume. This image is
# deliberately separate from the recorder (which starved today hosting an
# in-process shadow), the sports collector, the spring probe, the Gemini
# shadow and the l5-ops research service.
#
# It NEVER writes analysis/l5/** or the frozen model/lookup/spec: those
# files are copied in READ-ONLY for lookups; every output goes to the
# volume at /data/l5_shadow. A candidate lookup is a PROPOSAL file that
# requires an explicit owner decision (a reviewed, versioned commit).
FROM python:3.11-slim

WORKDIR /app
# pure stdlib: no pip install (no httpx/websockets/cryptography here)

# the loop and the helpers it reuses (all read-only research code)
COPY src/l5_shadow_loop.py src/l5_shadow_loop.py
COPY src/l5_model_drift_check.py src/l5_model_drift_check.py
COPY src/l5_shadow_scoring.py src/l5_shadow_scoring.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/l5_fidelity_gate.py src/l5_fidelity_gate.py
# the frozen lookup loader + the live sports decision layer (imported so the
# shadow normalizes with the SAME state_key / v2_dist objects; nothing armed)
COPY src/pmus_l5_persistence_v2.py src/pmus_l5_persistence_v2.py
COPY src/pmus_l5_fidelity_live.py src/pmus_l5_fidelity_live.py
COPY src/pmus_sports_l5_decision.py src/pmus_sports_l5_decision.py
COPY src/pmus_status_report.py src/pmus_status_report.py
# the sports event-progress tape and the venue progress reader it wraps.
# Imported lazily by the loop, so a missing COPY would fail at runtime
# rather than at import: both are pinned here on purpose.
COPY src/pmus_progress_tape.py src/pmus_progress_tape.py
COPY src/pmus_event_progress.py src/pmus_event_progress.py
# the existing collector + adapter + canonical row builder (never re-implemented)
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/config.py src/polymarket_l5/config.py
COPY src/polymarket_l5/ops.py src/polymarket_l5/ops.py
COPY src/polymarket_l5/adapters.py src/polymarket_l5/adapters.py
COPY src/polymarket_l5/action_model.py src/polymarket_l5/action_model.py
COPY src/polymarket_l5/realtime_mirror.py src/polymarket_l5/realtime_mirror.py
COPY src/polymarket_l5/realtime_mirror_base.py src/polymarket_l5/realtime_mirror_base.py
# frozen artifacts, READ-ONLY (model v2 / lookup v3, the frozen fidelity spec)
COPY analysis/l5/l5_action_model_v2.json analysis/l5/l5_action_model_v2.json
COPY analysis/l5/l5_fidelity_gate.json analysis/l5/l5_fidelity_gate.json
# canonical history, only for the in-container --dry-run smoke (fixture source)
COPY analysis/l5/l5_canonical_sequence.csv.gz analysis/l5/l5_canonical_sequence.csv.gz
RUN touch src/__init__.py

ENV L5_SHADOW_SERVICE_ROLE=l5-shadow-loop \
    L5_SHADOW_STATE_DIR=/data/l5_shadow \
    PYTHONUNBUFFERED=1

# boot tripwire (deploy/RAILWAY_ISOLATION.md pattern): the wrong image crashes
# visibly instead of running the wrong worker
CMD ["sh", "-c", "test \"$L5_SHADOW_SERVICE_ROLE\" = l5-shadow-loop || { echo SERVICE_ROLE_MISMATCH; exit 64; }; exec python3 -m src.l5_shadow_loop"]
