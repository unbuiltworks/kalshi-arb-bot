# L5_CONTINUOUS_FAITHFUL -- the frozen L5 process, UNMODIFIED, on Gemini.
#
# WHY THIS EXISTS. An independent review found that we had never actually
# tested the L5 continuous inventory process on Gemini: every Gemini run so
# far has been an ADAPTATION of it. That is correct, and the repository shows
# exactly where it happened -- in one argument.
#
#   tape_replay.POLICY_A = "FROZEN_L5_NATIVE"  the frozen policy, unchanged
#   tape_replay.POLICY_B = "GEMINI_RECYCLE"    once residual exists, quote the
#                                              COMPLETE leg and VETO the
#                                              EXTEND the frozen policy asks
#                                              for
#
# `shadow_soak --policy` DEFAULTS to FROZEN_L5_NATIVE. Every Gemini service we
# run overrides it to GEMINI_RECYCLE. Since L5's own evidence puts
# P(next action = EXTEND) at 0.66 and has 84.3% of imbalance episodes
# overshooting THROUGH zero, that override suppresses the dominant action of
# the process being replicated. This service removes the override and changes
# nothing else.
#
# NO NEW STRATEGY CODE. Every module here is the one already in the tree:
# l5_surface.engine.desired_surface emits the surface, continuous_probe.plan
# classifies rungs against inventory and applies the hard bounds,
# tape_replay.desired returns the frozen actions unfiltered under POLICY_A,
# and hypothetical.HypotheticalBook fills them under the CORRECTED symmetric
# queue model. Not one strategy line is added, removed or tuned.
#
# THE ECON ARMS ARE OFF. GEMINI_ECON_ARM=0 disables GEMINI_ECON_V1,
# PASSIVE_PAIR_V1 and the F0/F1/F2 ladder. Those carry the spread gate, the
# pair-economic floor and the flow gates -- all adaptations, none of them L5.
# They are not deleted; they keep running in gemini-econ-shadow.
#
# RISK IS NOT RELAXED. continuous_probe.risk_gates is untouched and still
# bounds imbalance (0.04), YES (0.10), NO (0.10) and total spend (0.15), and
# would_breach still refuses to rest an order it could not afford to have
# filled. Behavioural fidelity is not unlimited risk. Nothing here can be
# armed: shadow_soak constructs no session and has no order path.
#
#

# WHY IT IS A SECOND SERVICE RATHER THAN A REDEPLOY OF THE FIRST. The control
# `gemini-maker-shadow` has been serving a44de245 continuously and its
# watchPatterns are what have kept it pinned there while the branch moved on.
# Redeploying it to pick up the challenger would restart the control's own
# inventory walk and end the comparison this whole session exists to make. So
# the control is left exactly as it is and the challenger runs beside it,
# reading the same public tape from its own process.
#
# NOTHING HERE CAN BE ARMED. shadow_soak constructs no session and never
# references Driver.place or Driver.cancel -- asserted over the module AST by
# tests/test_gemini_shadow_soak_disarmed-style checks rather than by a flag.
# The econ package adds no transport of its own, and tests assert that its
# modules contain no order path, signer or credential.
FROM python:3.11-slim
WORKDIR /app
# The import closure, unchanged from the control's image. src/gemini_execution
# already carries the new econ/ package, so nothing is added here.
COPY src/__init__.py src/__init__.py
COPY src/gemini_execution/ src/gemini_execution/
COPY src/l5_surface/ src/l5_surface/
COPY src/l5_intent/ src/l5_intent/
COPY src/l5_shadow/ src/l5_shadow/
COPY src/l5_adapters/ src/l5_adapters/
COPY src/l5_fidelity/ src/l5_fidelity/
COPY src/l5_router/ src/l5_router/
COPY src/polymarket_l5/ src/polymarket_l5/
COPY src/gemini_prediction_auth.py src/gemini_prediction_auth.py
COPY src/l5_venue_adapter.py src/l5_venue_adapter.py
COPY src/auth.py src/auth.py
COPY src/fee_calc.py src/fee_calc.py
COPY artifacts/l5_surface/ artifacts/l5_surface/
RUN touch src/__init__.py && pip install --no-cache-dir httpx websockets
# A SEPARATE STATE ROOT. The control owns /state/BTC5m and /state/BTC15m and
# has been writing them since 2026-09-19; the challenger must not share, or a
# restart of either would read the other's window history as its own.
ENV PYTHONUNBUFFERED=1 \
    GEMINI_SHADOW_ROLE=gemini-l5-faithful \
    GEMINI_CANARY_STATE_DIR=/state/gemini-l5-faithful \
    GEMINI_ECON_ARM=0 \
    GEMINI_ECON_STATE_DIR=/state/gemini-l5-faithful/econ-state
CMD ["python3", "-m", "src.gemini_execution.shadow_soak", \
     "--cohorts", "btc5m,btc15m", "--policy", "FROZEN_L5_NATIVE", \
     "--windows", "0", "--cycles", "110", "--interval", "8.0", \
     "--state-root", "/state/gemini-l5-faithful"]
