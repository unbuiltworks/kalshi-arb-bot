# ARB Short-Crypto Market-Making / Temporal Strategy Lane

**Status:** SHADOW / READ-ONLY / NO ORDER AUTHORITY  
**Primary branch:** `ev-wallet-shadow`  
**Isolated Railway project:** `arb-ev-wallet-shadow`  
**Primary reference wallet:** Polymarket `@l5Zn1bWoM8eTsK` / `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`  
**User objective:** discover and safely reproduce scalable short-duration crypto economics across Polymarket, Gemini Predictions, and Kalshi without contaminating the main sports census or AWS sports-maker lane.

---

## 1. Workstream boundary

This lane is intentionally isolated from:

- the main 72-hour ARB sports census and its fixed verdict deadline;
- the AWS sports-maker migration / Gemini↔Kalshi sports latency experiment;
- existing sports live execution;
- the unrelated SOL executor.

Do **not** count any observation in this lane as user realized P&L until there is a confirmed live fill, settlement, and ledger entry from the user's own account.

Reference-wallet P&L is evidence about another public wallet, not our realized P&L.

Shadow fill candidates are not confirmed fills.

---

## 2. Current venue / asset matrix

### Polymarket

First-tier target matrix:

- BTC 5m
- BTC 15m
- ETH 5m
- ETH 15m
- SOL 5m
- SOL 15m
- XRP 5m
- XRP 15m

The live shadow currently discovers all eight streams.

### Gemini Predictions

Gemini's current prediction-market ticker specification supports short-duration `05M` and `15M` contracts and lists BTC, ETH, SOL and XRP as supported crypto underlyings. The planned first-tier matrix is therefore the same eight streams as Polymarket.

Gemini must be treated as an independent venue with its own fees, rebates, reward rules, strike capture, settlement process, queue behavior and P&L.

### Kalshi

Kalshi currently offers 15-minute crypto Up/Down markets. First-tier target matrix:

- BTC 15m
- ETH 15m
- SOL 15m
- XRP 15m

Additional Kalshi-only assets such as DOGE may be added after first-tier economics are measured.

Kalshi does **not** currently belong in the 5m matrix.

### Initial independent income-stream count

- Polymarket: 8 venue×asset×horizon streams
- Gemini Predictions: 8 venue×asset×horizon streams
- Kalshi: 4 venue×asset×horizon streams

**Total first-tier candidate streams: 20.**

These are independent accounting streams even when they share the same underlying crypto asset.

---

## 3. Reference-wallet strategy findings

### 3.1 The wallet is overwhelmingly maker-driven

Public Polymarket trade-feed comparison of the latest 10,000 fills:

- all observed fills: 10,000
- inferred maker fills: 8,328
- inferred taker fills: 1,672
- inferred maker fill share: **83.28%**
- maker-fill notional in sample: approximately **$76.3K**
- taker-fill notional in sample: approximately **$30.7K**

Method:

- compare public `/trades?user=<wallet>&takerOnly=false` against the same public query with `takerOnly=true`;
- rows present only in the all-trades sample are inferred maker fills by multiset difference.

This is still an inference from public API semantics and should be validated against authenticated venue execution-role data before making live execution decisions.

### 3.2 Activity is buy-and-hold-to-resolution, not sell-side scalping

Recent public wallet observations show almost exclusively BUY activity followed by REDEEM, rather than repeated SELL exits.

The strategy therefore appears to create economic value from:

1. passive maker fills / spread capture;
2. maker rebates;
3. liquidity rewards;
4. dynamic two-sided inventory construction;
5. directional residual inventory that resolves at expiry;
6. occasional aggressive taker fills to change inventory quickly.

### 3.3 Two-sided inventory is real, but not synonymous with locked arbitrage

The wallet frequently buys both outcomes in the same 5m/15m market.

However:

- chronological/FIFO pair accounting can be negative;
- aggregate side VWAP can sum above $1;
- the wallet can carry large directional residuals;
- ex-post cheapest/fungible pairing can show attractive sub-$1 pair inventory, but this uses hindsight and is not proof that those pairs were intentionally or atomically locked.

Therefore the current leading interpretation is **dynamic fair-value / maker inventory management**, not pure atomic temporal arbitrage.

### 3.4 5m and 15m behavior should be measured separately

A current 24-hour public economics reconstruction produced:

#### 5-minute resolved short-crypto markets

- resolved markets: **401**
- observed buy cost: approximately **$54,070.48**
- redeem value: approximately **$56,282.22**
- reconstructed trading P&L: approximately **+$2,211.74**

#### 15-minute resolved short-crypto markets

- resolved markets: **169**
- observed buy cost: approximately **$192,407.31**
- redeem value: approximately **$192,043.42**
- reconstructed trading P&L: approximately **-$363.89**

#### Combined resolved short-crypto trading

- resolved markets: **570**
- reconstructed trading P&L: approximately **+$1,847.85**

This suggests 5m and 15m should **not** be treated as the same strategy merely with a different horizon. The 5m lane currently appears much stronger on direct trading economics in this sample.

Boundary caveat: this is a conservative public-data ledger that excludes markets without an observed redemption in the target interval and does not force boundary inventory into P&L.

---

## 4. Incentive economics are a first-class component

### 4.1 Reference-wallet public incentive cashflows

Over the recent seven-day public-activity audit:

- explicit Polymarket `MAKER_REBATE`: approximately **$7,733.39**
- explicit Polymarket `REWARD`: approximately **$9,515.66**
- combined explicit incentive cashflows: approximately **$17,249.06**

The six maker-rebate transactions and six reward transactions were different transactions with different hashes and amounts, not duplicate representations of the same payouts.

### 4.2 Current 24-hour decomposition

In the current rolling 24-hour ledger:

- reconstructed resolved short-crypto trading P&L: **+$1,847.85**
- explicit maker rebate: **+$1,307.22**
- explicit liquidity reward: **+$1,489.25**
- reconstructed resolved trading + explicit incentives: **+$4,644.32**

This is evidence about the reference wallet only.

It implies the reference strategy is not adequately described by profile trading P&L alone. Incentives can be of similar or larger magnitude than the direct trading edge.

### 4.3 Current Polymarket reward-pool snapshot

At approximately 2026-08-27 08:16 ET, the reward-aware shadow observed these active market configurations:

- BTC 5m: $10,000/day visible pool, 50-share minimum, 1.5¢ max reward spread
- BTC 15m: $7,500/day visible pool, 50-share minimum, 1.5¢ max reward spread
- ETH 5m: ~$1,666.67/day
- ETH 15m: ~$833.33/day
- SOL 5m: ~$1,666.67/day
- SOL 15m: ~$833.33/day
- XRP 5m: ~$1,666.67/day
- XRP 15m: ~$833.33/day

The active eight-stream visible pools summed to approximately **$25,000/day** at that instant.

These are venue pool budgets, **not our expected earnings**. The pool is competitive and our share depends on relative quote quality, size, uptime and competing makers.

Pool values can change at any time and must always be read dynamically from the venue API.

---

## 5. Working strategy model

The current best model is:

```text
market opens
   ↓
maintain reward-eligible resting liquidity
   ↓
collect passive maker fills in small clips
   ↓
continually estimate fair Up/Down probability
   ↓
adjust quote skew and/or use selective taker orders
   ↓
maintain paired inventory + intentional directional residual
   ↓
hold contracts to resolution
   ↓
redeem winning inventory
   ↓
add maker rebate + liquidity reward economics
```

The strategy should therefore be evaluated with four separate P&L buckets:

1. **Maker trading / spread economics**
2. **Maker rebate income**
3. **Liquidity reward income**
4. **Residual inventory / directional P&L**

Total economics:

```text
TOTAL = MAKER_TRADING + MAKER_REBATES + LIQUIDITY_REWARDS + RESIDUAL_PNL
```

Do not collapse these into one opaque shadow-P&L number.

---

## 6. What is currently deployed

Railway project: `arb-ev-wallet-shadow`

Current services include:

### `polymarket-wallet-shadow`

- follows the reference wallet in read-only mode;
- records recent public trades and copyability observations;
- no order authority.

### `polymarket-temporal-shadow-run`

- older pure temporal-pair baseline;
- measures one-sided entries and subsequent pair completion;
- useful as a control strategy, but no longer assumed to represent the reference wallet's full mechanism.

### `polymarket-maker-econ-shadow`

New reward-aware active-window service.

Current behavior:

- discovers the eight active BTC/ETH/SOL/XRP 5m/15m Polymarket streams;
- reads active reward configs dynamically;
- reads books in parallel;
- maintains hypothetical resting bids on both outcomes;
- computes raw reward eligibility/score inputs;
- creates **cross-through fill candidates** only after a quote has rested and the current ask moves strictly through the hypothetical bid;
- tracks hypothetical two-sided inventory;
- no credentials, POST, signing, order cancel, or order-entry path.

Current runtime performance is approximately ~1–1.5 seconds for a full eight-market REST sampling cycle on Railway after parallelization.

That is acceptable for economic discovery, but **not** good enough to infer production maker fill quality.

### `polymarket-l5-econ-ledger`

Rolling 24-hour public-wallet economics service.

- reconstructs resolved 5m and 15m trading economics;
- separately adds explicit `MAKER_REBATE` and `REWARD` cashflows;
- refresh target: every 10 minutes;
- excludes unresolved/boundary positions rather than inventing P&L.

---

## 7. Railway vs AWS decision

### Railway is correct for now

Use the isolated Railway project for:

- wallet forensics;
- public activity ledgers;
- reward-pool discovery;
- slow/control shadows;
- strategy research;
- dashboards and summaries.

Reasons:

- already isolated from sports infrastructure;
- fast to deploy;
- no need to recreate environment today;
- no live trading credentials are needed for the current research stage.

### AWS is the intended hot path

Use AWS for:

- persistent WebSocket books;
- trade streams;
- queue-position simulation;
- quote/cancel latency measurement;
- fair-value signal ingestion;
- eventual maker canaries if separately authorized;
- eventual production execution only after evidence and risk review.

Do **not** move the current research collectors to AWS just for architectural neatness.

Move the latency-sensitive market-making shadow once its WebSocket/queue model exists, because that is where AWS can change the economics.

The existing AWS sports-maker host must not be disrupted; either isolate the process carefully or use a separate AWS service/instance when the crypto hot path is ready.

---

## 8. Major improvement required: stop relying on REST for execution-quality inference

The current Polymarket maker shadow is an economic probe, not a production simulator.

The next version should use persistent WebSockets and reconstruct the local book continuously.

Required market-data architecture:

```text
venue WS
  ↓
local order book
  ↓
quote state / queue model
  ↓
fill estimator
  ↓
inventory + reward score + fair value
  ↓
shadow economic ledger
```

A 1-second+ REST cycle can miss the exact adverse-selection dynamics that determine whether maker fills are profitable.

---

## 9. Major improvement required: queue-position-aware fill simulation

The current `cross-through` method is deliberately conservative but incomplete.

Next simulator should track:

- our hypothetical price;
- displayed size already ahead of us at that price;
- trade prints at/through that level;
- order-book size reductions;
- cancels ahead of us;
- time resting;
- whether enough executable flow occurred to consume the queue ahead;
- estimated partial-fill quantity.

Labels must remain explicit:

- `QUOTE_ELIGIBLE`
- `QUEUE_FILL_CANDIDATE`
- `SHADOW_PARTIAL_FILL`
- `SHADOW_FULL_FILL`
- never `FILLED` unless an actual authenticated order receipt confirms it.

---

## 10. Major improvement required: fair-value / inventory-skew model

Purely symmetric quoting is unlikely to reproduce the reference wallet.

The engine needs a fair probability for Up vs Down.

Candidate signals:

- venue's exact settlement reference / strike process;
- Gemini spot and order book;
- Coinbase / other exchange spot where useful;
- Chainlink/reference feed used by the prediction venue;
- time remaining to expiry;
- distance from opening/strike price;
- short-horizon realized volatility;
- book imbalance and recent trades.

The model should output:

```text
fair_up_probability
fair_down_probability = 1 - fair_up_probability
```

Then quote logic can decide:

- symmetric maker quote;
- skew Up;
- skew Down;
- stop quoting one side;
- use a taker inventory correction;
- stop taking directional residual risk near expiry.

This fair-value component must be separately validated; do not label it arbitrage.

---

## 11. Gemini Predictions replication

Gemini is a high-priority second venue because the current product structure is unusually compatible with this strategy.

Current first-tier Gemini streams:

- BTC 5m / 15m
- ETH 5m / 15m
- SOL 5m / 15m
- XRP 5m / 15m

Gemini supports public prediction-market WebSocket book/depth/trade streams and should use WebSocket as the primary active market-making data path.

Gemini also currently provides:

- a **Maker Rebate Program**;
- a **Liquidity Rewards Program**;
- a separate temporary **Taker Rewards Program** during the current published promotional period.

This makes Gemini a direct candidate for the same four-bucket economics model:

```text
maker trading + maker rebate + liquidity reward + residual inventory P&L
```

Do not assume Polymarket reward parameters transfer. Read Gemini's current program config dynamically.

### Gemini implementation order

1. market/event discovery for BTC/ETH/SOL/XRP 05M/15M;
2. strike retrieval;
3. public WebSocket local book;
4. reward-config reader;
5. generic reward-aware maker shadow;
6. queue/fill estimator;
7. fair-value / inventory-skew signal;
8. historical + forward economics by stream;
9. only then consider a q=1 live canary under separate authorization.

---

## 12. Kalshi replication

Current first-tier Kalshi streams:

- BTC 15m
- ETH 15m
- SOL 15m
- XRP 15m

Kalshi also currently has additional 15m crypto assets, but expand only after first-tier evidence.

Kalshi should be treated as its own strategy instance, not as a cross-venue hedge by default.

Kalshi has:

- public 15m crypto markets;
- a Liquidity Incentive Program that can reward resting orders in eligible markets;
- a separate designated Market Maker Program;
- market-specific maker/taker fees that must be read and modeled correctly.

### Kalshi implementation order

1. dynamic discovery of current 15m series and fees;
2. exact settlement-source/rule audit per series;
3. WebSocket local book and trade stream;
4. liquidity-incentive eligibility/config reader;
5. generic reward-aware maker shadow;
6. queue/fill estimator;
7. fair-value / inventory-skew model;
8. economics by BTC/ETH/SOL/XRP stream;
9. only then q=1 canary under separate authorization.

---

## 13. Generic architecture target

Do **not** build three unrelated bots.

Build one venue-agnostic strategy core with adapters:

```text
                  ┌─ Polymarket adapter
MarketMakerCore ──┼─ Gemini Predictions adapter
                  └─ Kalshi adapter
```

Generic core responsibilities:

- `MarketSpec`
- `RewardSpec`
- `FeeModel`
- `LocalBook`
- `QuotePolicy`
- `QueueEstimator`
- `InventoryLedger`
- `FairValueModel`
- `SettlementSpec`
- `EconomicLedger`
- `RiskBudget`

Venue adapters own:

- ticker/market discovery;
- WebSocket protocol;
- order-book semantics;
- fee formula;
- reward configuration;
- order protocol;
- settlement source/rules.

This keeps all venue×asset streams independently measurable while avoiding three divergent codebases.

---

## 14. Risk architecture

Even though each venue/asset/horizon is a separate income stream, underlying exposure is correlated.

Central risk must aggregate:

- BTC residual exposure across all venues/horizons;
- ETH residual exposure across all venues/horizons;
- SOL residual exposure across all venues/horizons;
- XRP residual exposure across all venues/horizons;
- total outstanding quote notional;
- total filled but unpaired inventory;
- maximum loss at simultaneous adverse settlement.

A three-venue deployment must not accidentally triple directional BTC risk simply because each worker is individually inside its local limit.

---

## 15. Required scorecard

Every venue×asset×horizon stream should report:

### Market-making quality

- quote uptime
- average distance from midpoint
- reward-eligible uptime
- average quoted size
- queue position estimate
- shadow fill candidates
- estimated partial/full fills
- maker/taker mix

### Trading economics

- maker fill VWAP
- settlement P&L
- residual inventory P&L
- pair cost where applicable
- adverse-selection markout at 80ms / 250ms / 500ms / 1s

### Incentive economics

- visible reward pool
- raw reward score
- estimated relative reward share (when defensible)
- modeled maker rebate
- actual maker rebate after live activation
- modeled liquidity reward
- actual liquidity reward after live activation

### Capacity

- capital required
- contracts/day
- turnover/day
- expected net $/day
- depth-constrained scalable $/day
- maximum one-sided exposure
- loss tail / drawdown

---

## 16. Promotion gates

### SHADOW

No credentials/order authority.

Require:

- stable market discovery;
- local book integrity;
- settlement mapping;
- fee model;
- reward model;
- queue-aware fill model;
- economic decomposition.

### LIVE PROBE

Requires a separate explicit authorization and must be q=1 / minimal size.

Before promotion:

- positive forward shadow economics after inventory losses;
- reward assumptions not required to hide negative trading economics unless reward economics themselves are robust and obtainable by us;
- acceptable adverse-selection markout;
- no settlement ambiguity;
- capital reservation and kill switch;
- authenticated reconciliation.

### SCALE

Scale only on our actual realized ledger.

Do not scale because the reference wallet makes money.

Required evidence:

- confirmed maker fills;
- confirmed rebate/reward receipts;
- settled P&L;
- stable inventory tail;
- demonstrated capacity at increased quote size.

---

## 17. Near-term engineering priority

Priority by expected impact on safe realized dollars/day:

1. **Polymarket WebSocket + queue-aware maker shadow** for all BTC/ETH/SOL/XRP 5m/15m streams.
2. **Reference-wallet economics decomposition by asset + horizon + time-to-expiry + maker/taker role.**
3. **Gemini Predictions adapter** for all 8 first-tier streams, including its current maker rebate and liquidity reward programs.
4. **Kalshi 15m adapter** for BTC/ETH/SOL/XRP, including its liquidity incentive and fee rules.
5. **Fair-value / quote-skew model** using exact settlement reference plus fast spot lead signals.
6. Move the latency-sensitive shadows to **AWS** and compare Railway vs AWS queue/fill/markout quality.
7. Rank all **20 first-tier streams** by defensible net economics and scalable $/day.
8. Only then request authorization for q=1 canaries on the top-ranked streams.

---

## 18. Current interpretation

The strategy discovery has moved from:

`pure temporal arbitrage`

into:

`high-frequency reward-aware market making + inventory management + selective taker correction`.

Pure temporal pair-locking remains a useful control strategy and may itself be profitable in some streams, but it is no longer the leading explanation for the reference wallet.

The most important current evidence is:

- maker fills dominate the reference wallet sample;
- direct 5m trading was strongly positive in the current 24h resolved sample;
- direct 15m trading was slightly negative in that sample;
- explicit maker rebates and liquidity rewards were large enough to materially change total economics;
- all three target venues now have short-duration crypto markets suitable for independent maker/temporal testing;
- Polymarket and Gemini have especially direct reward/rebate structures; Kalshi also has liquidity incentives that must be measured independently.

The next bottleneck is **execution-quality measurement**, not idea generation.
