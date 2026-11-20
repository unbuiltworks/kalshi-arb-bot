# L5 strategy and execution handoff — 2026-11-20

**Status:** evidence-based handoff for ChatGPT, Claude Code, and the next conversation. **Not trading authorization.** This document separates observed facts, supplied harvest summaries, hypotheses, and implementation decisions. Verify fresh GitHub/Railway/account state before acting. No deployment or live trading is authorized by this document.

## Mission and decision hierarchy

Build ONE continuously operating, inventory-aware BTC5m prediction-market trader using existing L5 infrastructure. Long-term target: **$480/day confirmed settled realized net profit**, not theoretical edge, order count, or shadow returns. Profitability outranks imitation, throughput, and infrastructure. Claude Code is primary implementation agent; ChatGPT reviews evidence and coordinates. No separate Bonereaper bot.

**Critical correction:** Do NOT assert Bonereaper's BTC5m strategy systematically earns money on matched pairs. A prior assistant incorrectly recommended "retaining profitable pair accumulation" as the essence of Bonereaper replication; the supplied 304-window harvest contradicts this. Treat the distinction as a mandatory preflight for every strategy recommendation.

## Bonereaper BTC5m evidence — user-supplied harvest summary, not independently re-audited here

| Period | Windows | Median matched pair unit cost | Windows with pair cost < $1 | Matched pair P&L |
|---|---:|---:|---:|---:|
| March 2026 | 30 | $1.029 | 26.7% | −$1,588 |
| August 2026 | 274 | $1.015 | 43.1% | −$6,489 |
| Combined March 25–August 31, 2026 | 304 | $1.020 | 41.4% | −$8,078 |

**Supported interpretation:** two-sided accumulation continues; matched pair cost was generally above $1; matched pairs were a negative aggregate P&L contributor. A profitable matched-pair arbitrage policy is NOT the observed primary economic mechanism. Account-level profitability, if any, would need to come from residual inventory outcomes and/or credited incentives or other components. **Not established by this table:** actual account-level net P&L, residual contribution, incentive amounts, transferability to PMUS, whether particular negative-cost pairs were optimal at decision time, and a validated probability model. Reconcile the underlying harvest, methodology, and fee/rebate accounting before stronger claims.

Behavioral hypotheses worth studying: simultaneous UP/DOWN maker quotes, repeated fills/replenishment, repricing, accumulation with intentional temporary imbalance, selective residual exposure, and incentives. Historical competitor behavior is not proof of positive expected value.

## L5 BTC5m venue-confirmed live windows (reported in project evidence)

| Market start UTC | Matched shares | Matched P&L | Unmatched position | Unmatched P&L | Net settled |
|---|---:|---:|---|---:|---:|
| 2026-11-20 14:20 | 0.16 | $0.0000 | 0.06 DOWN | −$0.0114 | −$0.0114 |
| 2026-11-20 17:05 | 0.41 | +$0.0296 | 0.44 DOWN | −$0.0447 | −$0.0151 |
| Combined | | +$0.0296 | | −$0.0561 | **−$0.0265** |

At 17:05: 39 submitted, 21 filled (7 UP / 14 DOWN), 18 cancelled, zero uncertain/open; UP 0.41 shares cost $0.2677, DOWN 0.85 cost $0.1574; total $0.4251, redemption $0.41, fees $0, no credited rebates. Venue cash $321.4668 → $321.4517. FIFO attribution matched venue position record. The 14:20 window's cash change was −$0.0114. These are **two small windows**, not proof of sustainable edge or a validated strategy. L5's positive matched contribution is NOT evidence Bonereaper's matched contribution is positive.

Execution milestone: 17:05 window repeatedly quoted both sides, replenished/repriced, recycled reservations (submitted $0.9768 cumulatively within $0.60 outstanding envelope), had no envelope refusals, and settled cleanly. Book-read timeout guard was not exercised live. No profit claim from submitted orders or counterfactual fills.

## Engineering state and evidence pointers

- Repository: `unbuiltworks/kalshi-arb-bot`.
- Last **reported deployed** BTC5m SHA: `27cbdb32bb86d74e406dee1fd0a3829a1f4776ac` (reliability/hold-release fixes); latest reported lane state after 17:05 window: `MONITOR`, disarmed, `can_submit:false`, zero open orders, watchdog healthy. **Recheck live status before any action.**
- Working branch commit `ab1a809208f31ba53b3eeffe0f4143c8ecdc210d`: one-sided control, FIFO settlement attribution, duplicate own-loss accounting correction; **not deployed as of the report**.
- Relevant files: `analysis/l5/btc5m/LIVE_WINDOW_PACKET.md`, `analysis/l5/btc5m/evidence/window_1705z_orders.json`, `analysis/l5/btc5m/evidence/window_1705z_one_sided_replay.json`, `src/l5_btc5m_attribution.py`, BTC5m controller/runner and associated tests.
- One-sided gate `L5_BTC5M_STRADDLE_ONE_SIDED` is **off by default**. When partner cannot be quoted, unmatched-increasing buy requires fresh independent side probability exceeding price by 0.06 adverse selection + 0.01 minimum edge. It adds refusals, not a new order path. 109 tests and five mutation checks reported. The replay refused 10 of 13 lone DOWN orders and 7 of 8 corresponding lone fills, yielding an **arithmetic counterfactual** of about +$0.021 rather than −$0.0151. **Not a venue result, causal fill simulation, or evidence of edge.** Some allowed orders rested on a questionable model probability.
- Settlement attribution: 21 tests reported; duplicate daily-loss fix: 13 tests reported. Reported targeted suite retains 29 pre-existing failures; confirm exact baseline parity and current CI before promoting.
- Quote shading exists but was not enabled; pair-completion cost/value is reporting-only; no new sell/taker order path.
- A separate **unmerged, undeployed** `l5-pmus-live` cash-only floor patch at `analysis/l5/btc5m/patches/pmus_live_uncertain_cash_floor.patch` changes how uncertain account value is published. **Do not include or deploy without independent safety review and explicit owner approval**: verify actual venue buying-power semantics and drawdown fail-closed behavior. 135 equity tests were reported. An intermittent ~16s portfolio-value publication gap and double daily-loss count were observed.
- The normal entry-EV gate lacks attested conditional-on-fill rates; prior two live windows used narrowly approved single-market EV waivers for execution validation. Do not generalize or silently renew a waiver.

## Strategic decision: what to optimize next

**Do not make sub-$1 pair completion the primary Bonereaper replication objective.** Also do not copy negative-EV pair accumulation just to imitate him. Evaluate whole-window economics: matched-pair cost/P&L + residual settlement contribution + venue-confirmed fees/rebates/incentives − adverse selection and risk. Study whether the source of any competitor account-level profit is residual exposure, incentives, or something else, using auditable harvest components.

The new one-sided gate is a candidate *risk/economics control*, not a proven replication improvement. It may reduce losses in one window but suppress productive intentional imbalance. Compare it to the existing controller using the same evidence and explicit counterfactual limitations. Do not promote based solely on hindsight outcome. Require credible at-decision probability quality and conditional-on-fill evidence before asserting edge.

Highest-impact next executable engineering step: inspect current GitHub PRs/issues, Railway deployed SHA and mode, account risk and settled P&L; review `ab1a80920` and its tests; prepare a **disarmed** release candidate for the non-controversial attribution and duplicate-loss changes, and keep one-sided control off until independently evaluated. Keep the cash-floor patch separate. Any money-capable deployment or live probe needs new explicit owner authorization.

## Safety and authorization

Never infer permission to trade from permission to research, code, test, or prepare. No further window is authorized by the prior 17:05 approval. Preserve account capital limits, daily and cumulative loss halts, exact-order cancellation, durable reconciliation, independent watchdog, shared order ownership, maker-only/taker-off status unless explicitly changed, and fail-closed economics. No new services or prolonged shadow campaigns by default.

Before every operational recommendation/report, state: running mode + deployed SHA (verified vs reported), actual code/test changes, venue-confirmed fills, **settled realized net P&L**, primary blocker, next executable action, authorization needed, and infrastructure cost. Mark all unverified values as reported or unknown.

## Mandatory evidence discipline for ChatGPT and Claude

1. Check this handoff and the underlying harvest before strategy recommendations. If advice conflicts with historical evidence, identify the conflict explicitly.
2. Separate **observed venue results**, **user-supplied harvested statistics**, **model estimates**, and **counterfactual replays**.
3. Distinguish matched-pair P&L from total net P&L; never conflate L5's two positive/breakeven pair windows with Bonereaper's negative 304-window matched result.
4. Never invent fill probabilities, incentives, profitability, or current runtime state.
5. Fix the highest-impact bottleneck; preserve working infrastructure; do not start over.

## New-chat starting instruction

"Continue L5 using `analysis/l5/btc5m/L5_STRATEGY_HANDOFF_20261120.md` as the durable handoff. First verify current GitHub, Railway, account authorization, and settled net P&L. Crucial correction: the user-supplied Bonereaper BTC5m harvest (304 windows) shows **−$8,078 matched-pair P&L**, median pair cost **$1.020**, and only **41.4%** of windows with pair cost below $1. Do not characterize profitable pair accumulation as Bonereaper's demonstrated core strategy. Identify the economic mechanism, assess the one-sided control without hindsight, preserve risk gates, and advance one integrated controller. No live trading without fresh explicit approval."
