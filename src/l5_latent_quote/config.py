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

# ---- order lifecycle
MAX_REST_S = 120.0                 # then EXPIRED; no silent reprice
DISCOVERY_DAYS = 3                 # first 3 distinct UTC days are DISCOVERY

# ---- output, entirely our own
OUT_DIR = "data/l5_latent_quote"
