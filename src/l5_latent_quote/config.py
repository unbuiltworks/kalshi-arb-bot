"""Frozen parameters. Changing any of these starts a new preregistration block."""
from __future__ import annotations

# ---- identity
L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
SERIES_SLUG_PREFIX = "btc-updown-15m-"
WINDOW_SECONDS = 900

# ---- public read-only endpoints (same venue L5 trades on)
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
USER_AGENT = "curl/8.5.0"          # the agent proxy answers 403 without one

# ---- cadence, frozen
BOOK_POLL_S = 2.0
DECISION_S = 5.0
HORIZONS_S = (5, 10, 30, 60)

# ---- venue facts, read from the live book and asserted at runtime
TICK = 0.01
MIN_ORDER_SIZE = 5.0

# ---- price arms
ARMS = ("P1_AT_BID", "P2_BEHIND_BID", "P3_IMPROVE_BID", "P4_BASIS", "P5_STATE")
PAIR_COST_CAP = 0.96               # spring-discipline figure, L5-derived
SHADOW_SIZE = 5.0                  # one min-size clip; never scaled by results

# ---- side models
SIDE_MODELS = ("S0_PRIOR", "S1_FLOW", "S2_CHEAP_H1", "S3_PAIRCOST_H3",
               "S4_H1_H3", "S5_FULL")
CONF_BINS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01))
ABSTAIN_BELOW = 0.50               # ABSTAIN is a first-class outcome

# ---- order lifetime arms, FROZEN. Decision cadence and resting-order lifetime
# are different things: constant repricing forfeits queue position and would
# destroy the maker mechanism the experiment exists to reproduce.
#
# R3 durations are derived from L5's OWN observed inter-action timing in the
# canonical corpus (p75 = 8s, p90 = 20s, outer bound 120s), measured in
# analysis/l5/next_action/. They are NOT derived from any prospective P&L, and
# all three run in parallel rather than one being selected afterwards.
LIFETIME_ARMS = (
    "R1_STICKY",            # rest until filled, invalidated, side-reversed, or window end
    "R2_MATERIAL_CHANGE",   # reprice only on side change / rule violation / no longer passive
    "R3_FIXED_8",           # L5 inter-action p75
    "R3_FIXED_20",          # L5 inter-action p90
    "R3_FIXED_120",         # long rest, the outer bound
    "R4_REPRICE_5S",        # control arm: the previous every-decision behaviour
)
R3_DURATIONS = {"R3_FIXED_8": 8.0, "R3_FIXED_20": 20.0, "R3_FIXED_120": 120.0}
MAX_REST_S = 900.0                 # hard ceiling: one window
WINDOW_END_CANCEL = True           # all arms cancel at window expiry

# ---- which side models carry resting orders (the rest are scored on
# calibration only, which needs no order)
EXECUTION_MODELS = ("S1_FLOW", "S5_FULL")

DISCOVERY_DAYS = 3                 # first 3 distinct UTC days are DISCOVERY

# ---- ghosts: a cancelled order keeps being observed to price the cost of
# forfeiting queue position. A ghost NEVER affects inventory or economics.
GHOST_OBSERVE_S = 300.0
GHOST_MAX_OBSERVING = 300          # bounded; evictions are emitted, not dropped

# ---- output, entirely our own
OUT_DIR = "data/l5_latent_quote"
