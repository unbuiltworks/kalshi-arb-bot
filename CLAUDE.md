# Kalshi Arb Bot

## What This Is

A single-platform arbitrage bot for Kalshi prediction markets. It buys YES + NO on the same market when YES_ask + NO_ask + total_fees < $1.00, locking a guaranteed profit since settlement always pays $1.00 on one side and $0.00 on the other.

This is NOT a directional/prediction bot. It has no opinion on outcomes. It captures mechanical mispricings.

## Architecture

```
kalshi-arb-bot/
├── CLAUDE.md              # You are here
├── .env                   # API credentials (never commit)
├── .env.example           # Template for credentials
├── requirements.txt       # Python dependencies
├── src/
│   ├── __init__.py
│   ├── main.py            # Entry point, orchestrates everything
│   ├── auth.py            # RSA-PSS signing, header generation
│   ├── ws_client.py       # WebSocket connection, subscriptions, reconnect
│   ├── orderbook.py       # Local orderbook state from snapshots + deltas
│   ├── fee_calc.py        # Kalshi fee math (taker + maker)
│   ├── spread_scanner.py  # Computes edge on every book update
│   ├── order_manager.py   # Paired order execution (maker leg → taker leg)
│   ├── risk.py            # Filters, position limits, cooldowns
│   └── logger.py          # Structured logging to file + stdout
├── config/
│   ├── settings.py        # All tunable parameters in one place
│   └── markets.py         # Market discovery and filtering
└── tests/
    ├── test_fee_calc.py
    ├── test_spread_scanner.py
    └── test_orderbook.py
```

## Environment

- **Language**: Python 3.11+
- **Key dependencies**: `websockets`, `httpx` (async HTTP), `cryptography` (RSA-PSS signing), `python-dotenv`
- **No frameworks** — this is a lightweight async bot, not a web app

## Kalshi API Details

### Base URLs
- **Demo REST**: `https://demo-api.kalshi.co/trade-api/v2`
- **Demo WebSocket**: `wss://demo-api.kalshi.co/trade-api/ws/v2`
- **Prod REST**: `https://api.elections.kalshi.com/trade-api/v2`
- **Prod WebSocket**: `wss://api.elections.kalshi.com/trade-api/ws/v2`

### Authentication
RSA-PSS signing with SHA-256. Every request (REST and WS handshake) requires:
```
KALSHI-ACCESS-KEY: <api_key_id>
KALSHI-ACCESS-SIGNATURE: <base64_rsa_pss_signature>
KALSHI-ACCESS-TIMESTAMP: <unix_ms>
```

Signature message format: `{timestamp_ms}{METHOD}{path}` (e.g. `1234567890GET/trade-api/ws/v2`)

### WebSocket Channels Needed
1. **`orderbook_delta`** (requires auth) — gives initial snapshot then incremental updates per market_ticker
2. **`fill`** (requires auth) — notifies when our orders fill

Subscribe format:
```json
{
  "id": 1,
  "cmd": "subscribe",
  "params": {
    "channels": ["orderbook_delta", "fill"],
    "market_tickers": ["TICKER-1", "TICKER-2"]
  }
}
```

Message types to handle:
- `orderbook_snapshot` — full book state, rebuild local book
- `orderbook_delta` — incremental update, apply to local book
- `fill` — our order was filled, trigger second leg or log
- `error` — handle gracefully

### Order Creation (REST)
```
POST /trade-api/v2/portfolio/orders
```
```json
{
  "ticker": "MARKET-TICKER",
  "action": "buy",
  "side": "yes",
  "type": "limit",
  "yes_price": 65,
  "count": 10,
  "client_order_id": "uuid",
  "post_only": true
}
```

Key fields:
- `side`: "yes" or "no"
- `action`: "buy" (always buy for arb — buy YES and buy NO)
- `yes_price`: price in CENTS (integer). If buying NO, you still specify `yes_price` — the NO price is `100 - yes_price`
- `post_only`: true for maker leg (rejected if would cross), false/omit for taker leg
- `client_order_id`: UUID for tracking, required

### Order Cancellation (REST)
```
DELETE /trade-api/v2/portfolio/orders/{order_id}
```

### Fee Structure (effective Feb 5, 2026)
**Taker fee**: `ceil(0.07 × C × P × (1 - P))` — rounds UP to next cent
**Maker fee**: `ceil(0.0175 × C × P × (1 - P))` — rounds UP to next cent

Where:
- P = price of contract in dollars (50 cents = 0.50)
- C = number of contracts

Special lower fees for S&P500 (INX*) and NASDAQ-100 markets: 0.035 taker rate.

**No settlement fee. No membership fee.**

Fee examples for 100 contracts:
| Price | Taker Fee | Maker Fee |
|-------|-----------|-----------|
| $0.10 | $0.63     | ~$0.16    |
| $0.25 | $1.32     | ~$0.33    |
| $0.50 | $1.75     | ~$0.44    |
| $0.75 | $1.32     | ~$0.33    |
| $0.90 | $0.63     | ~$0.16    |

Fees are CONVEX — maximized at P=0.50. Lopsided markets (one side near 0 or 1) have lower fees.

## Core Logic: The Spread Scanner

On every orderbook update for a tracked market:

```python
yes_ask = best_ask_price_yes  # cheapest YES available
no_ask = best_ask_price_no    # cheapest NO available (= 100 - best_yes_bid... BUT read from the book directly)

# For arb, we need to BUY both sides
# Kalshi's book is quoted in YES terms:
#   - YES ask = cheapest price to buy YES
#   - NO ask = 100 - best YES bid (because selling YES = buying NO)
# WAIT — that's wrong for arb. We need the actual NO ask.
# On Kalshi, to BUY NO contracts, we look at the NO side of the book.
# The orderbook_delta gives us yes_bids and yes_asks (or no_bids and no_asks).
# A NO ask IS the complement: if someone is bidding YES at 60, then NO is available at 40.
# 
# CRITICAL UNDERSTANDING:
# YES ask = lowest price someone is selling YES at
# To buy NO, we can either:
#   (a) Buy NO directly at the NO ask, OR
#   (b) Sell YES at the YES bid — which is economically equivalent
#
# For arb: total_cost = yes_ask + (100 - yes_bid)  [in cents]
# Edge = 100 - yes_ask - (100 - yes_bid) = yes_bid - yes_ask... wait that's negative normally
#
# CORRECT FORMULATION:
# To lock arb: buy YES at yes_ask AND buy NO at no_ask
# On Kalshi's unified book: no_ask = 100 - yes_bid
# Total cost per contract (cents) = yes_ask + (100 - yes_bid)
# Total cost per contract (cents) = yes_ask - yes_bid + 100
# This equals 100 + spread, so it's >100 when spread is positive (normal market)
# ARB EXISTS when: yes_ask + (100 - yes_bid) + fees < 100
# Equivalently: yes_ask < yes_bid - fees  (crossed book after fees)
# This is a NEGATIVE spread situation — very rare but possible during fast moves

# ALTERNATIVE ARB (more common):
# Some Kalshi events have MULTIPLE markets (e.g., "above X" and "below X")
# Or the YES/NO on the SAME market can misprice transiently
# The scanner should check: yes_best_ask + no_best_ask + total_fees < 100

quantity = min(depth_at_yes_ask, depth_at_no_ask, max_position_size)
yes_fee = calc_taker_fee(quantity, yes_ask / 100)  # or maker fee if posting
no_fee = calc_taker_fee(quantity, no_ask / 100)     # or maker fee if posting
total_cost_cents = (yes_ask * quantity) + (no_ask * quantity) + yes_fee + no_fee
guaranteed_payout = 100 * quantity  # $1 per contract

edge_cents = guaranteed_payout - total_cost_cents

if edge_cents > min_edge_threshold:
    # SIGNAL: execute paired trade
```

### Maker-First Execution Strategy

To minimize fees, we want one leg as maker (post_only) and one as taker:

1. **Post maker leg** — the side with more depth (more likely to fill). Use `post_only: true`.
2. **Watch for fill** via WebSocket `fill` channel.
3. **On fill → immediately fire taker leg** on the other side. No `post_only`.
4. **Timeout** — if maker leg doesn't fill within `MAX_FILL_WAIT_MS`, cancel it.
5. **Legging risk** — if maker fills but taker fails/slips, we hold directional exposure. Must have a flatten rule.

### Flatten Rule (Critical Safety)
If we're legged (one side filled, other didn't):
- Immediately attempt to exit the filled leg at breakeven or small loss
- Log the incident
- Increment cooldown counter
- If cooldown threshold hit, pause bot for `COOLDOWN_PERIOD_MS`

## Config Parameters (config/settings.py)

```python
# Environment
ENV = "demo"  # "demo" or "prod"

# API
API_KEY_ID = ""      # from .env
PRIVATE_KEY_PATH = "" # from .env

# Scanning
MARKETS_TO_TRACK = []           # populated by market discovery
MAX_MARKETS = 50                # don't subscribe to too many
SCAN_INTERVAL_MS = 0            # 0 = scan on every book update (event-driven)

# Execution  
MIN_EDGE_CENTS = 3              # minimum profit per contract after ALL fees
MIN_DEPTH = 10                  # minimum contracts available on both sides
MAX_POSITION_PER_MARKET = 100   # max contracts per market
MAX_TOTAL_POSITION = 500        # across all markets
ORDER_SIZE = 10                 # contracts per trade
MAKER_FIRST = True              # post maker leg first

# Risk
MAX_FILL_WAIT_MS = 5000         # cancel maker leg if not filled
MAX_LEGS_OPEN = 1               # only one pending arb at a time
COOLDOWN_AFTER_LEG_FAIL = 30000 # ms to pause after legging incident
MAX_LEG_FAILS_BEFORE_HALT = 3   # halt bot after this many legging incidents
SPREAD_FLARE_THRESHOLD = 0.10   # skip if spread > 10% of price
MAX_DAILY_LOSS_CENTS = 5000     # $50 daily loss limit, halt bot

# Logging
LOG_FILE = "arb_bot.log"
LOG_LEVEL = "INFO"
LOG_EVERY_SCAN = False          # True = log every book update (verbose)
LOG_OPPORTUNITIES = True        # always log when edge is detected
```

## Market Discovery (config/markets.py)

On startup, the bot should:
1. `GET /trade-api/v2/markets?status=open&limit=200` — fetch all open markets
2. Filter for markets with reasonable volume/liquidity
3. Subscribe to `orderbook_delta` for filtered markets
4. Re-scan periodically (every 5 min) to pick up new markets

Good arb candidates:
- Recently opened markets (prices haven't settled)
- Markets with multiple market makers
- Events with many related markets (bracket-style)
- NOT long-dated markets with no activity

## Build Order (for Claude Code)

Build in this order. Each step should be testable independently.

### Step 1: Auth + REST client
- `src/auth.py` — RSA-PSS signing, header generation
- Basic REST client in `src/main.py` that can hit `GET /exchange/status`
- Test: run and confirm authenticated response from demo API

### Step 2: Fee calculator
- `src/fee_calc.py` — implement taker_fee() and maker_fee() 
- Must handle the round-up (ceil) behavior
- Must handle special INX/NASDAQ100 rates
- `tests/test_fee_calc.py` — validate against the known fee table values

### Step 3: WebSocket client
- `src/ws_client.py` — connect, auth, subscribe, reconnect with backoff
- Handle: orderbook_snapshot, orderbook_delta, fill, error messages
- Test: connect to demo, subscribe to one market, print updates

### Step 4: Local orderbook
- `src/orderbook.py` — maintain bid/ask state per market from snapshots + deltas
- Methods: `best_yes_ask()`, `best_yes_bid()`, `best_no_ask()`, `depth_at_price()`
- `tests/test_orderbook.py`

### Step 5: Spread scanner
- `src/spread_scanner.py` — on every book update, compute edge
- Wire to orderbook updates
- Log every opportunity found
- Test: feed synthetic book data, verify edge calculation

### Step 6: Order manager
- `src/order_manager.py` — paired execution logic
- Post maker leg → wait for fill → fire taker leg
- Cancel on timeout
- Track open orders, fills, positions
- Flatten rule for legging

### Step 7: Risk layer
- `src/risk.py` — all filters and circuit breakers
- Position limits, daily loss, cooldowns, spread flare check
- Wire into order manager as pre-trade checks

### Step 8: Main loop
- `src/main.py` — orchestrate everything
- Startup: auth → discover markets → connect WS → subscribe → scan loop
- Graceful shutdown on SIGINT

## Testing Strategy

- **Unit tests**: fee_calc, orderbook, spread_scanner (with synthetic data)
- **Integration test**: connect to demo WS, subscribe, verify book updates arrive
- **Paper trading**: run full bot on demo with real market data, log all signals and what trades WOULD have executed
- **Only after paper trading looks clean**: switch ENV to "prod"

## Important Gotchas

1. **Kalshi prices are in CENTS (integers)**. yes_price=65 means $0.65. Don't mix dollars and cents.
2. **Orderbook is quoted in YES terms**. A NO ask = 100 - YES bid. Make sure the spread scanner handles this correctly.
3. **post_only orders are rejected if they would immediately match**. This is what we want for maker legs — guarantees maker fee, not taker.
4. **client_order_id is required** and must be unique (use UUID4).
5. **Rate limits exist** — don't spam order creation. The bot should be event-driven (react to book updates), not polling.
6. **WebSocket can disconnect** — implement reconnect with exponential backoff (1s, 2s, 4s, 8s, max 30s).
7. **Fee rounding is UP (ceil), not nearest**. This matters — a $0.001 theoretical fee rounds to $0.01. Every cent counts in arb.
8. **Legacy integer fields being removed March 12, 2026** — use the `_fp` (fixed-point string) and `_dollars` fields instead of integer cents fields where available.
9. **Batched orders endpoint is now GA** — `POST /portfolio/orders/batched` could be useful for placing both legs simultaneously, reducing legging risk. Investigate this.

## Logging Requirements

Every log line should include: timestamp, market_ticker, event type.

Log these events at INFO level:
- Opportunity detected (edge, quantity, yes_ask, no_ask, fees)
- Order placed (side, price, quantity, client_order_id)
- Order filled (side, price, quantity, fill_id)
- Order cancelled (reason)
- Legging incident (which side filled, which didn't)
- Bot halt (reason)

Log at DEBUG:
- Every book update (if LOG_EVERY_SCAN=True)
- Fee calculations
- Filter rejections (why an opportunity was skipped)
