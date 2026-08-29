"""Static configuration for the L5 read-only historical exporter/analyzer.

Nothing in this package authenticates, signs, places, cancels, or otherwise
mutates anything. It reads Polymarket's public HTTP APIs and writes local
files only.
"""

from __future__ import annotations

# --- Target trader -----------------------------------------------------------

# Proxy wallet the public data API keys activity on.
TARGET_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
# Public profile handle (used only for provenance in the raw cache, not for
# lookups -- the data API is addressed by wallet).
TARGET_PROFILE = "l5zn1bwom8etsk"

# --- Public API bases --------------------------------------------------------

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

USER_AGENT = "kalshi-arb-research/l5-historical (read-only)"

# Page sizes. The data API caps /trades and /activity at 500 per page; gamma
# caps /markets at 500. We request the cap and stop when a short page comes
# back.
PAGE_LIMIT = 500
# Public endpoints stop honouring `offset` past roughly this depth, so the
# fetcher walks backwards in time instead of paging forever. See fetch.py.
MAX_OFFSET = 10_000

# --- Scope -------------------------------------------------------------------

ASSETS = ("BTC", "ETH")
WINDOW_MINUTES = (5, 15)

# A window is accepted as 5m/15m only if the *derived* duration from market
# metadata lands inside one of these bands. Titles are cross-checked, never
# trusted on their own (see classify.py).
WINDOW_DURATION_BANDS_S = {
    5: (270, 330),    # 300s +/- 30s
    15: (840, 960),   # 900s +/- 60s
}

# --- Analysis parameters -----------------------------------------------------

# Price bands for "did he buy at >= 70c / 80c / 90c" and per-band quantities.
PRICE_BAND_EDGES = (0.0, 0.70, 0.80, 0.90, 1.01)
PRICE_BAND_LABELS = ("lt70", "70to80", "80to90", "gte90")
ESCALATION_THRESHOLDS = (0.70, 0.80, 0.90)

# A window counts as "balanced" when the residual directional inventory is
# within this fraction of the larger leg.
BALANCED_TOLERANCE_FRAC = 0.05
# ...and always balanced if the absolute residual is under this many shares,
# which keeps dust from flipping a 2000-share paired window to "directional".
BALANCED_TOLERANCE_SHARES = 1.0

# Two trades on opposite sides within this many seconds are treated as
# contemporaneous for the "did he buy the cheaper side" cross-check.
CONTEMPORANEOUS_WINDOW_S = 30

# Bootstrap resamples for confidence intervals, and the seed that makes every
# reported interval reproducible.
BOOTSTRAP_N = 4000
BOOTSTRAP_SEED = 20260829

# Minimum sample size before a comparison is reported as anything other than
# "insufficient data".
MIN_SAMPLE_FOR_CLAIM = 20

# --- Local paths -------------------------------------------------------------

CACHE_DIR = "data/l5/cache"
OUTPUT_DIR = "data/l5"

RAW_ACTIVITY_JSONL = "raw_activity.jsonl"
RAW_TRADES_JSONL = "raw_trades.jsonl"
RAW_POSITIONS_JSONL = "raw_positions.jsonl"
RAW_MARKETS_JSONL = "raw_markets.jsonl"
FETCH_MANIFEST_JSON = "fetch_manifest.json"

OUT_ALL_TRADES = "l5_all_trades.csv"
OUT_WINDOW_SEQUENCES = "l5_window_sequences.csv"
OUT_WINDOWS_SUMMARY = "l5_windows_summary.csv"
OUT_ANALYSIS_JSON = "l5_analysis.json"
