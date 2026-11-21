# L5 BTC 5-minute live session. ONE owner-approved window, then idle.
#
# MONEY-CAPABLE when the owner supplies PMUS credentials and arms it. It
# submits only through `l5_btc5m_execution`, which spends the owner's
# single-use authorization claim before every order; the transport arms only
# after its own preflight passes AND the owner's arm token is present. With
# either missing it sends nothing.
#
# Separate service from l5-pmus-live. It shares no volume with it. It reads
# that service's account-wide loss state (L5_ACCOUNT_RISK_URL) and the shared
# halt record the independent watchdog asserts (L5_HALT_URL), and refuses to
# submit when either cannot be read.
#
# The COPY list is the import closure of the entrypoint plus the modules it
# imports lazily on the submit, approval, ARMING and halt paths; tests keep it
# so (tests/test_l5_btc5m_image_deps.py walks the lazy imports too:
# `authorize_arming` imports l5_mandate_signature unconditionally and
# l5_promotion_gate under a mandate, and neither was shipped before).
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
# THIRD-PARTY DEPENDENCIES of the shipped modules, the same pins as the
# l5-pmus-live image. `pmus_auth` signs every authenticated venue request
# with `cryptography` (Ed25519); `l5_pmus_auth_book` reads the authenticated
# socket with `websockets`. Both are imported lazily, so their absence did
# not show at import time: on 2026-11-12 03:45Z every authenticated call
# (account, positions, orders, settlement) raised ModuleNotFoundError, the
# preflight failed and the window never armed. A test now requires every
# third-party import in the shipped files to be installed here.
RUN pip install --no-cache-dir "websockets>=12" "cryptography>=42"
COPY src/__init__.py src/__init__.py
COPY src/l5_btc5m_controller.py src/l5_btc5m_controller.py
COPY src/l5_behaviour_probe.py src/l5_behaviour_probe.py
# the controller's two candidate generators. Pure, stdlib-only, and
# imported by it, so the image must carry them or the runner dies on
# import -- which is what test_the_image_ships_the_whole_closure pins.
COPY src/l5_late_favorite.py src/l5_late_favorite.py
COPY src/l5_strategy_arbiter.py src/l5_strategy_arbiter.py
COPY src/l5_straddle_ev.py src/l5_straddle_ev.py
COPY src/l5_venue_constraints.py src/l5_venue_constraints.py
COPY src/l5_measured_entry_rates.py src/l5_measured_entry_rates.py
COPY src/l5_shared_reservation.py src/l5_shared_reservation.py
COPY src/l5_btc5m_attribution.py src/l5_btc5m_attribution.py
COPY src/l5_live_safety.py src/l5_live_safety.py
COPY src/l5_order_registry_server.py src/l5_order_registry_server.py
COPY src/l5_persistent_mode.py src/l5_persistent_mode.py
COPY src/l5_btc5m_execution.py src/l5_btc5m_execution.py
COPY src/l5_5m_fleet.py src/l5_5m_fleet.py
COPY src/l5_btc5m_runner.py src/l5_btc5m_runner.py
COPY src/l5_btc_reference.py src/l5_btc_reference.py
COPY src/l5_campaign_approval.py src/l5_campaign_approval.py
COPY src/l5_defensive_completion.py src/l5_defensive_completion.py
COPY src/l5_execution_translations.py src/l5_execution_translations.py
COPY src/l5_faithful_translation.py src/l5_faithful_translation.py
COPY src/l5_fidelity_mode.py src/l5_fidelity_mode.py
COPY src/l5_halt_gate.py src/l5_halt_gate.py
COPY src/l5_managed_lanes.py src/l5_managed_lanes.py
COPY src/l5_live_authorization.py src/l5_live_authorization.py
COPY src/l5_live_execution.py src/l5_live_execution.py
COPY src/l5_mandate_signature.py src/l5_mandate_signature.py
COPY src/l5_order_body.py src/l5_order_body.py
COPY src/l5_series_registry.py src/l5_series_registry.py
COPY src/l5_origin.py src/l5_origin.py
COPY src/l5_pmus_auth_book.py src/l5_pmus_auth_book.py
COPY src/l5_pmus_transport.py src/l5_pmus_transport.py
COPY src/l5_preview_gate.py src/l5_preview_gate.py
COPY src/l5_promotion_gate.py src/l5_promotion_gate.py
COPY src/l5_queue_measurement.py src/l5_queue_measurement.py
COPY src/l5_taker_completion_policy.py src/l5_taker_completion_policy.py
COPY src/l5_two_sided_probe.py src/l5_two_sided_probe.py
COPY src/pmus_auth.py src/pmus_auth.py
COPY src/pmus_fees.py src/pmus_fees.py
#: reached lazily by l5_btc5m_execution.completion_taker_fault for the
#: all-in taker pair economics; it imports only pmus_fees, shipped above
COPY src/pmus_q1_executor.py src/pmus_q1_executor.py
#: reached lazily by completion_taker_fault for `walk_own_side` -- the
#: depth walk the pmus-live lane prices against, reused rather than
#: reimplemented so the two lanes cannot disagree about how much
#: displayed depth to believe. Imports l5_two_sided_probe, shipped above.
COPY src/l5_inventory_decision.py src/l5_inventory_decision.py
#: the residual phase projection, read lazily in the step report
COPY src/l5_residual_phase.py src/l5_residual_phase.py
COPY src/pmus_ws_evidence_recorder.py src/pmus_ws_evidence_recorder.py
CMD ["python3", "-u", "-m", "src.l5_btc5m_runner"]
