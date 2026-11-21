# Gemini Subsidy-Economics Metric Spec (frozen 2026-08-31)

Source: audit discussion following the L5 full-history ledger
(docs/L5_FULL_HISTORY_LEDGER_20260831.md). Declared BEFORE the 21:10 UTC
C-series read and BEFORE the Sept 2 live rebate verification, so the live
q10 test is scored against predeclared metrics.

## The reframed optimization problem

Old question: can we get both sides filled cheaply enough to create
trading profit?

New question: **can we generate lots of safe maker turnover without
one-sided fills eating the subsidy?**

Three simultaneous requirements:
1. **Enough maker fills** — rebates pay on executed maker fills; a
   beautiful untouched quote earns rebate-nothing (LR is the separate
   exception below).
2. **Enough two-sided completion** — repeated YES fills without NO fills
   is a directional bet, not a subsidy farm. L5 tolerated −1.9¢/residual-$
   because Polymarket paid ~1.4¢/$ total; Gemini's tolerance is unknown
   until measured.
3. **The right kind of fills** — a high fill rate composed of
   adversely-selected fills (filled just before the move against us) is
   worse than fewer neutral fills.

Ideal cycle at q10: quote both sides → YES maker fill → NO strike-zone
maker fill within seconds → pair cost ≤ $1.00 → trading P&L ≈ $0 → maker
fees $0 (post Sept 2) → both fills earn rebate → repeat. Zero trading
edge required.

## Production metrics (in priority order)

Per session, per market, and per size tier:

1. `maker_fill_rate` — maker fills / quotes posted (and per quote-hour)
2. `maker_to_maker_completion_rate` — first maker fill followed by an
   opposite-side MAKER fill within the window
3. `time_to_opposite_fill_s` — distribution, not just median
4. `one_sided_residual_dollars` — residual inventory at window end
5. `residual_loss_per_turnover` — settlement-scored residual loss / $1 of
   maker turnover
6. `rebate_credited_per_turnover` — ACTUAL credited rebate / $1 turnover
   (live API, never the formula)
7. `lr_credited_per_quoted` — actual Liquidity Rewards credited per $1
   quoted and per $1 turnover
8. `net_subsidy_minus_losses` — subsidy − residual loss − pair overpay

**North star: `realized_net_cents_per_maker_turnover_dollar`** (settlement-
scored, all cashflows, fail-closed).

Sensitivity anchor: at +1¢/$, $50k/day of recycled turnover ≈ $500/day.
At +0.2¢/$ the same target needs $240k/day. Negative ⇒ no amount of
scaling fixes it.

## The adverse-selection guard

Raw fill count is NOT a success metric. Every fill-rate number must be
paired with post-fill markout (5s/15s/30s executable) and completion
fillability — the maker-specific labels already specified for the
next-move study. `subsidy_earned_per_fill / inventory_loss_created_by_fill`
is the ratio that decides whether a fill source is worth quoting into.

## Two potentially distinct businesses

- **A. Rebate churn** — needs executed maker fills (metrics 1–6).
- **B. Liquidity farming** — LR scores resting two-sided quotes + uptime
  (min size 10, ≤10¢ from mid, 50% uptime, 1.5× two-sided); income can
  accrue with FEW fills. If LR pools are generous at our sizes, safe
  sitting quotes may beat fill-chasing.

Do not assume either pays: both require actually-credited amounts from the
live account (Sept 2 onward). Formula-modeled subsidies are not evidence
(standing rule: nobody scales on modeled rebates).

## How tonight's C-series maps onto this

The sealed C-series (read 21:10 UTC — untouched by this spec) measures the
mechanical core of requirement 2: whether a first maker fill reliably
becomes a second maker fill instead of residual inventory
(maker_flatten_pct, pair_completion_pct, residual, settlement-scored
economics), with C10 at the first subsidy-meaningful size tier. It cannot
measure metrics 6–7 (credited subsidies) — those need the live account and
Sept 2. A C10 pass + a ≥~1¢/$ measured subsidy stack is the go condition
for an L5-style lane; either failing is a fast, cheap no.
