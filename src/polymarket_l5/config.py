"""Static configuration for the L5 historical study.

Everything tunable lives here so the analysis is reproducible from a single
place. No secrets, no credentials -- this package only reads public data.
"""

from __future__ import annotations

from pathlib import Path

# --- Subject -----------------------------------------------------------------

TARGET_ADDRESS = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
TARGET_PROFILE_SLUG = "l5zn1bwom8etsk"

# --- Public API hosts (GET only, allowlisted) --------------------------------

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

ALLOWED_HOSTS = frozenset(
    {
        "data-api.polymarket.com",
        "gamma-api.polymarket.com",
        "clob.polymarket.com",
    }
)

# Paging. The data API caps `limit`; we page with offset until a short page.
PAGE_LIMIT = 500
MAX_PAGES = 400  # hard stop so a misbehaving endpoint cannot loop forever
REQUEST_TIMEOUT_S = 30.0
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_S = (2.0, 4.0, 8.0, 16.0)

# --- Scope -------------------------------------------------------------------

ASSETS = ("BTC", "ETH")
WINDOW_BUCKETS_MIN = (5, 15)
# A market's measured duration must land within this many seconds of a bucket
# for the duration-based classification to be accepted.
WINDOW_TOLERANCE_S = 45

# --- Price bands -------------------------------------------------------------

# Threshold flags the study explicitly asks about.
PRICE_THRESHOLDS = (0.70, 0.80, 0.90)

# Disjoint bands used for "quantity bought in each price band".
PRICE_BANDS = (
    ("lt50", 0.00, 0.50),
    ("50_59", 0.50, 0.60),
    ("60_69", 0.60, 0.70),
    ("70_79", 0.70, 0.80),
    ("80_89", 0.80, 0.90),
    ("90_plus", 0.90, 1.01),
)

# --- Paths -------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "l5"
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
OUTPUT_DIR = REPO_ROOT / "analysis" / "l5"

TRADES_CSV = "l5_all_trades.csv"
SEQUENCES_CSV = "l5_window_sequences.csv"
SUMMARY_CSV = "l5_windows_summary.csv"
EXCLUSIONS_CSV = "l5_excluded_markets.csv"
STATS_JSON = "l5_analysis_stats.json"
MANIFEST_JSON = "l5_manifest.json"

# --- Numeric hygiene ---------------------------------------------------------

QTY_EPS = 1e-9
