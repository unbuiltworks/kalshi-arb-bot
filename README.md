# Getting Started

## Prerequisites
- Python 3.11+
- Kalshi demo account with API key (RSA key pair)
- Claude Code installed (`npm install -g @anthropic-ai/claude-code`)

## Setup

```bash
# Clone or copy this project
cd kalshi-arb-bot

# Create virtualenv
python -m venv venv
source venv/bin/activate

# Install deps
pip install -r requirements.txt

# Copy env template and fill in your credentials
cp .env.example .env
# Edit .env with your KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH
```

## Generate Kalshi API Keys (Demo)

1. Go to https://demo.kalshi.co (or prod: https://kalshi.com)
2. Account → API Keys → Generate new key
3. Download the private key PEM file
4. Save it to this project directory
5. Update `.env` with the key ID and path

## Building with Claude Code

The `CLAUDE.md` file in this project root is your spec. Claude Code reads it automatically.

```bash
# Start Claude Code in the project directory
cd kalshi-arb-bot
claude

# Then tell it what to build, step by step:
```

### Suggested prompts in order:

**Step 1** — "Build src/auth.py with RSA-PSS signing and a basic REST client. Test it by hitting GET /exchange/status on demo."

**Step 2** — "Run the fee calculator tests. Fix any failures." (tests/test_fee_calc.py already exists)

**Step 3** — "Build src/ws_client.py — async WebSocket client that connects to demo, subscribes to orderbook_delta for one market ticker, and prints incoming messages. Include reconnect with exponential backoff."

**Step 4** — "Build src/orderbook.py — maintains local orderbook state from snapshot and delta messages. Methods: apply_snapshot(), apply_delta(), best_yes_ask(), best_no_ask(), depth_at_ask(). Write tests."

**Step 5** — "Build src/spread_scanner.py — on every book update, compute arb edge using fee_calc. Emit opportunities as events. Wire it to the orderbook."

**Step 6** — "Build src/order_manager.py — paired order execution. Maker-first with post_only on one leg, taker on the other after fill confirmation via WebSocket. Include timeout/cancel and flatten rule."

**Step 7** — "Build src/risk.py — pre-trade filters (min edge, min depth, position limits, daily loss, cooldown). Wire into order_manager."

**Step 8** — "Build src/main.py — full orchestration. Startup sequence: auth → discover markets → connect WS → subscribe → run scan loop. Graceful shutdown on Ctrl+C."

## Running

```bash
# Paper trading mode (logs signals, doesn't execute)
python -m src.main --paper

# Live demo trading
python -m src.main

# Run tests
pytest tests/ -v
```

## Monitoring

Logs go to `arb_bot.log` and stdout. Key things to watch:
- Opportunity frequency (how often does edge appear?)
- Fill rate on maker legs
- Legging incidents (should be near zero)
- Realized edge vs. theoretical edge

## Sportsbook + Kalshi cross-market shadow scanner

This branch adds a **shadow-only** scanner for the adjacent-total structure:

- sportsbook `OVER` at one or more half-point totals
- Kalshi `NO` on `total > H`
- maximize the worst-case dollar profit after configured Kalshi taker fees
- report the extra payoff when the final score lands in the middle

The sportsbook feed uses the current OpticOdds v3 SSE API (the OddsJam API developer link redirects to OpticOdds). It ingests live odds, alternate totals, limits, and deep links. Kalshi is maintained from the authenticated `orderbook_delta` WebSocket using current fixed-point snapshot/delta shapes, subscription-wide sequence validation, and explicit `use_yes_price=true`. A sequence gap invalidates all local books and forces a reconnect/fresh snapshot. **No sportsbook bet and no Kalshi order is submitted.**

### Configure

```bash
cp config/cross_market_pairs.example.json config/cross_market_pairs.json
# Add explicit fixture_id <-> Kalshi ticker pairs and set enabled=true.

export OPTICODDS_API_KEY="..."
export CROSS_MARKET_SPORTSBOOKS="Caesars,DraftKings,BetMGM,Fanatics"
```

Every enabled pair must include `kalshi_taker_fee_rate`. Do not blindly copy the example value: Kalshi documents that some series have different fee schedules, including special sports events. Verify the current fee schedule for the exact series before treating the scanner's floor as locked profit. The scanner fails closed when the fee rate is omitted.

Explicit event pairing is also a safety control. We do not fuzzy-match games into a "guaranteed" trade because mismatched games, lines, overtime treatment, or settlement rules destroy the hedge.

### Run

```bash
python -m src.cross_market_shadow \
  --bankroll-cents 100000 \
  --min-profit-cents 2500 \
  --min-roi 0.03 \
  --max-quote-age-ms 1500 \
  --scan-interval-ms 50
```

The 50 ms scan loop only reads already-maintained local state; it does not poll Kalshi REST every 50 ms.

The log emits:

- `OPEN`: a qualifying worst-case floor exists after the configured Kalshi fee rate
- `MOVE`: prices/stakes changed while the edge remains open
- `CLOSE`: edge disappeared, including measured lifetime in milliseconds

That gives us the data needed before considering any execution: opportunities per game/day, floor ROI distribution, capital required, available size, and edge lifetime.
