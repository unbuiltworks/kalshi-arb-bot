import os
from dotenv import load_dotenv

load_dotenv()

# --- Environment ---
ENV = os.getenv("KALSHI_ENV", "demo")

# --- API ---
API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./private_key.pem")

BASE_URLS = {
    "demo": {
        "rest": "https://demo-api.kalshi.co/trade-api/v2",
        "ws": "wss://demo-api.kalshi.co/trade-api/ws/v2",
    },
    "prod": {
        "rest": "https://api.elections.kalshi.com/trade-api/v2",
        "ws": "wss://api.elections.kalshi.com/trade-api/ws/v2",
    },
}

REST_URL = BASE_URLS[ENV]["rest"]
WS_URL = BASE_URLS[ENV]["ws"]

# --- Market Discovery ---
MAX_MARKETS = 50
MARKET_REFRESH_INTERVAL_S = 300  # re-discover markets every 5 min
MIN_MARKET_VOLUME = 100          # skip dead markets

# --- Scanning ---
MIN_EDGE_CENTS = 3               # min profit per contract after ALL fees (cents)
MIN_DEPTH = 10                   # min contracts on both sides
SPREAD_FLARE_THRESHOLD_PCT = 10  # skip if spread > this % of midprice

# --- Execution ---
ORDER_SIZE = 10                  # contracts per trade
MAKER_FIRST = True               # post maker leg first to save fees
MAX_POSITION_PER_MARKET = 100    # max contracts held per market
MAX_TOTAL_POSITION = 500         # max contracts across all markets

# --- Risk ---
MAX_FILL_WAIT_MS = 5000          # cancel maker if not filled in this window
MAX_CONCURRENT_ARBS = 1          # only one pending arb at a time
COOLDOWN_AFTER_LEG_FAIL_MS = 30000
MAX_LEG_FAILS_BEFORE_HALT = 3
MAX_DAILY_LOSS_CENTS = 5000      # $50 daily loss => halt

# --- WebSocket ---
WS_RECONNECT_BASE_S = 1.0
WS_RECONNECT_MAX_S = 30.0
WS_PING_INTERVAL_S = 20

# --- Logging ---
LOG_FILE = "arb_bot.log"
LOG_LEVEL = "INFO"
LOG_EVERY_SCAN = False
LOG_OPPORTUNITIES = True

# --- Fee Rates ---
# Standard markets
TAKER_FEE_RATE = 0.07
MAKER_FEE_RATE = 0.0175

# S&P500 (INX*) and NASDAQ-100 markets get reduced taker rate
REDUCED_TAKER_FEE_RATE = 0.035
REDUCED_FEE_PREFIXES = ("INX", "NASDAQ100")
