# L5 RESEARCH SUPERVISOR -- one process, every research stream, read-only.
#
# Owns and restarts:
#   exec_shadow     dual-venue executable L5 shadow (+ prospective maker-fill
#                   evidence, resolved from venue prints only)
#   gemini_depth    Gemini websocket book + paired REST evidence
#   chain_validate  chain observer settled-window cross-validation
#   book_context    Polymarket BTC5m/BTC15m full-book + print tape + chain
#                   parent receipts, recorded continuously
#   gemini_surface  Gemini passive quote-surface shadow
#   window_forensics  L5_WINDOW_FORENSICS_V1, the frozen preregistered window
#                   study -- run verbatim, one fresh segment per start
#
# and records its OWN downtime as downtime, so an absence of observations can
# never be read as NO ACTION or NO FILL.
#
# READ ONLY BY CONSTRUCTION. Unauthenticated GETs and one public market-data
# websocket. No credential is read, no order verb exists, and the COPY list
# below is deliberately narrow:
#   * src/l5_adapters/evaluate.py is ABSENT -- it imports l5_venue_adapter,
#     which lazily reaches pmus_q1_executor, the order path.
#   * no src/pmus_*, no src/kalshi_*, no signer, no wallet module.
# tests/test_l5_research_supervisor.py asserts the closure in a subprocess, so
# an edit that reintroduces one fails CI rather than shipping.
FROM python:3.11-slim
WORKDIR /app
# the only third-party dependency: the Gemini market-data socket
RUN pip install --no-cache-dir "websockets==12.0"
ENV PYTHONUNBUFFERED=1

COPY src/__init__.py src/__init__.py
COPY src/l5_research_supervisor.py src/l5_research_supervisor.py
COPY src/l5_shadow/ src/l5_shadow/
COPY src/l5_intent/ src/l5_intent/
COPY src/l5_router/ src/l5_router/
COPY src/l5_fidelity/ src/l5_fidelity/
COPY src/l5_chain_observer/ src/l5_chain_observer/
COPY artifacts/l5_fidelity/ artifacts/l5_fidelity/
# only the two adapter modules the shadow imports; see the note above
COPY src/l5_adapters/__init__.py src/l5_adapters/__init__.py
COPY src/l5_adapters/base.py src/l5_adapters/base.py
COPY src/l5_adapters/gemini_mapping.py src/l5_adapters/gemini_mapping.py
# Gemini's vendored fee schedule only
COPY src/polymarket_l5/__init__.py src/polymarket_l5/__init__.py
COPY src/polymarket_l5/transfer_backtest.py src/polymarket_l5/transfer_backtest.py
# the collectors that live under analysis/ (namespace packages)
COPY analysis/l5/gemini_depth/probe.py analysis/l5/gemini_depth/probe.py
COPY analysis/l5/gemini_depth/report.py analysis/l5/gemini_depth/report.py
COPY analysis/l5/gemini_depth/flow.py analysis/l5/gemini_depth/flow.py
COPY analysis/l5/chain_observer/validate.py analysis/l5/chain_observer/validate.py
# the preregistered gate, reported by the supervisor on a slow cadence. A READ
# over the volume's own stores: it promotes nothing and reaches no network.
COPY analysis/l5/chain_observer/gate.py analysis/l5/chain_observer/gate.py
COPY analysis/l5/chain_observer/PROMOTION_GATE_PREREGISTERED.md analysis/l5/chain_observer/PROMOTION_GATE_PREREGISTERED.md
# P2: the economic ledger, reported on the same slow cadence as the gate.
# Reads the shadow's own stores plus the venue's published resolutions.
COPY analysis/l5/economics/build.py analysis/l5/economics/build.py
# P1: the same frozen intents under both observation clocks. Reads stores
# already written; the chain observer stays unpromoted.
COPY analysis/l5/dual_clock/study.py analysis/l5/dual_clock/study.py
# P0: pair formation -- the promotion-critical statistic -- and the
# reactive vs state-driven arm comparison.
COPY analysis/l5/pair_formation/study.py analysis/l5/pair_formation/study.py
COPY analysis/l5/pair_formation/arms.py analysis/l5/pair_formation/arms.py
# BTC15m arm scoring (R/S/D) on inventory rather than quote fill rate.
COPY analysis/l5/pair_formation/arm_score.py analysis/l5/pair_formation/arm_score.py
# the depth frontier: is there a sweet spot, or is deeper just cheaper?
COPY analysis/l5/pair_formation/frontier.py analysis/l5/pair_formation/frontier.py
# ADAPTED: the frozen bounded-residual arms cap5/cap8/cap13. Reporting only --
# these read the same evidenced-fill stream and write their own store. They
# carry no order verb and no credential, so the read-only closure is unchanged.
COPY analysis/l5/adapted/collect.py analysis/l5/adapted/collect.py
COPY analysis/l5/adapted/prospective.py analysis/l5/adapted/prospective.py
COPY analysis/l5/adapted/failure.py analysis/l5/adapted/failure.py
COPY analysis/l5/coverage.py analysis/l5/coverage.py
# P0 2026-09-17: the two prospective recorders, moved here after they died
# twice inside the session container. Their import closure was checked in a
# fresh subprocess and contains no order verb, no signer and no credential
# reader; tests/test_recorder_watchdog.py asserts that every module the
# supervisor can spawn is actually present in this list, so a stream cannot be
# registered and then silently fail to import in the image.
COPY src/l5_book_context.py src/l5_book_context.py
COPY src/gemini_l5_surface_shadow.py src/gemini_l5_surface_shadow.py
COPY src/l5_residual_episode_clock.py src/l5_residual_episode_clock.py
COPY analysis/l5/causal/order_id_forensics.py analysis/l5/causal/order_id_forensics.py
COPY analysis/l5/residual_exit/forensics.py analysis/l5/residual_exit/forensics.py
COPY analysis/l5/redemption/audit.py analysis/l5/redemption/audit.py
# The frozen preregistered window study, moved here 2026-09-17 after the
# session container killed it mid-capture. Its module is UNCHANGED -- sha256
# 48214f23bfaee4f7f3999f017fd435c1babac8b68e808debf586777737ce68e6 -- and the
# supervisor runs it with its exact frozen argv. Its closure adds only the
# passive-surface hypotheses it predicts from; no order verb, no credential.
COPY src/l5_window_forensics.py src/l5_window_forensics.py
COPY src/l5_passive_surface_shadow.py src/l5_passive_surface_shadow.py
# what this image was built from, for provenance. No .git ships, and
# the supervisor reads no environment variable.
COPY deploy/l5_exec_shadow/RELEASE RELEASE

CMD ["python", "-m", "src.l5_research_supervisor", \
     "--state-root", "/state/l5-research"]
