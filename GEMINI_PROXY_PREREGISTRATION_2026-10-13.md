# Gemini Maker-Print Proxy — Frozen Pre-Registration

**Committed BEFORE any markout was computed against official settlement.** Timestamp of freeze: 2026-10-13, prior to harvesting the holdout log blocks. Purpose: make the holdout legitimate. Nothing below may be altered after holdout results are seen; any change requires a new pre-registration file and must be disclosed.

## Settlement source (official, not proxy)

Event resolution recovered from committed repository evidence on `claude/gemini-btc-economic-viability-h3u7zl`, `analysis/gemini/**`: fields `resolutionSide`, `settlementValue`, `resolvedAt`. 201 distinct resolved event tickers.

Ticker mapping (empirically confirmed): market symbol `GEMI-<EVENT>-UP` → event ticker `<EVENT>`, i.e. strip the `GEMI-` prefix and the `-UP` side suffix. Example: `GEMI-BTC15M2609221300-UP` → `BTC15M2609221300`.

Payout semantics: the series is quoted on the **UP** instrument. `resolutionSide == "yes"` ⇒ UP pays $1.00, `"no"` ⇒ UP pays $0.00. A window with `resolutionSide == null` is **excluded**, never imputed.

## Selection criteria (frozen)

1. Window must have official resolution (`resolutionSide` ∈ {yes, no}).
2. Window must have ≥1 logged two-sided quote and ≥1 logged print.
3. **Causality:** the reference quote must *strictly precede* the print — quote `tte > print tte`. The quote used is the most recent such quote.
4. **Freshness:** quote age `≤ 3.0 s` (`quote_tte − print_tte ≤ 3.0`). Staler quotes are dropped, not extrapolated.
5. Quote must be strictly two-sided and non-inverted: `ask > bid`.
6. **No terminal-quote requirement.** (Removed — it was a mis-specification; hold-to-settlement needs no quote near expiry.)

## Maker-fill classification (frozen)

- print price `≤ bid` ⇒ proxy fill of our resting **BUY** at `bid` (`we_buy`)
- print price `≥ ask` ⇒ proxy fill of our resting **SELL** at `ask` (`we_sell`)
- print strictly inside the spread ⇒ **excluded** (no maker fill implied)

Our fill price is the **quoted touch**, not the print price. Every such fill is a **proxy**: a print at our level does not prove our order would have filled. Queue priority is unknown.

## P&L (frozen)

Per contract, UP instrument, held to official settlement:
- `we_buy`:  `payout − bid`
- `we_sell`: `ask − payout`   (short the UP contract at the touch)
where `payout ∈ {1.00, 0.00}` from `resolutionSide`.

Size: `min(print_qty, displayed_depth_on_our_side)` — we cannot fill more than was displayed.

## Fees (frozen, transaction-level rounding)

- maker fee `= ceil(0.0175 · C · P · (1−P) · 100)/100`, rounded **UP** to the cent
- maker rebate `= floor(0.30 · 0.07 · C · P · (1−P) · 100)/100`, rounded **DOWN** to the cent; applied **only** if `0.20 ≤ P ≤ 0.80`; capped at 5% of fill notional
- Two regimes reported separately:
  - **PROMO** (to 2026-12-31): maker fee = 0
  - **POST-PROMO** (from 2027-01-01): maker fee as above
- **Rebates are reported as a separate line and excluded from the headline** unless account eligibility is demonstrated. Account tier is unverified.
- No minimum execution size is imposed. Size-dependent fee effects are reported as a cost curve, not a strategy rule.

## Fill-rate bounds (frozen)

Headline reports **100% proxy fill** (an upper bound by construction) and the conservative band **0–30%** applied to capacity. No point fill estimate is claimed.

## Samples (frozen split, declared before measurement)

- **DISCOVERY:** windows resolving on 2026-09-21 and 2026-09-22.
- **HOLDOUT:** windows resolving on 2026-09-23, 2026-10-03, 2026-10-04 — not inspected until discovery results are final.
- Reported per cell: BTC 5m, BTC 15m, ETH 5m, ETH 15m.
- Independence unit is the **market-window**, never the one-second snapshot. Window counts are reported alongside fill counts, and concentration is reported (share of P&L from the top window and top fill).

## Declared limitations

- One-sided book is **not observable**: 100% of 44,005 logged series rows are two-sided; one-sided observations are counted but their prices are not logged. One-sided making can be neither evaluated nor excluded here.
- Settlement set is limited to the 201 events previously stored; it is not the full venue history.
- `settlementValue` is an index value, not a contract payout; payout is taken from `resolutionSide` only.
