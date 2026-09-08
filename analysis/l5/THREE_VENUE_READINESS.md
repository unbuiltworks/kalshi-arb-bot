# Three-venue readiness — Polymarket US, Kalshi, Gemini

_OWNER #167 comment 5584095017. One L5 behavioural brain, three execution
environments — not three strategies and not cross-venue arbitrage._

Read-only survey. Nothing here deploys live money, and nothing here changes
PMUS R5F. Live venue evidence was pulled from Kalshi's public API on
2026-09-08; every figure below either cites that evidence or is marked as
not established.

## The matrix

| | **Polymarket US (PMUS)** | **Kalshi** | **Gemini** |
|---|---|---|---|
| **Market lane** | sports (ATP/WTA, MLB, CFB, CS2/LoL, table tennis) | crypto 15-minute up/down: `KXBTC15M`, `KXETH15M` | crypto 5m/15m BTC/ETH |
| **Implementation** | `pmus_l5_continuous_session` + `pmus_spring_l5_session` over the frozen model | `src/kalshi_l5_shadow.py` — a **batch tape analyser**, not a service | `src/gemini_l5_shadow_v2.py` + `gemini_l5_shadow_service.py` |
| **Service** | `pmus-spring-l5-probe` (LIVE, armed), `pmus-ws-evidence-recorder`, `l5-shadow-loop` | **none** | `gemini-l5-shadow` (running, 0.245 GB) |
| **Deploy config** | `deploy/pmus_spring_l5` | **none** | `deploy/gemini_l5_shadow` |
| **Data availability** | authenticated WS recorder, 24/7 | public REST reachable and rich (see below); **no collector exists** | public WS collector (`policy_d_port/collector.py`), not itself deployed |
| **L5 clock fidelity** | **broken** — 635/635 decisions key `tte>10m`; translation built and gated (`L5_STATE_TRANSLATION.md`) | **native** — venue serves `frequency: fifteen_min`, `open_time` → `close_time` exactly 15 min, deterministic settlement. Literal TTE is correct; the sports translation must NOT be applied | **native** — real 5m/15m windows |
| **Fees (ordinary account)** | `fees_usd: venue_unreported` on every settlement row | per-series metadata: `fee_type: quadratic`, `fee_multiplier: 1` on both lanes | known and modelled in the existing shadow |
| **Maker fees** | not separable from venue data | the venue **names** maker-charging series: only 2 of 274 crypto series are `quadratic_with_maker_fees`, and neither is a 15M lane — see the caveat below | modelled |
| **Book / flow** | thin, one-sided at times; the binding problem | `KXBTC15M-26SEP080800-00`: yes 0.30 × 1146.12 / 0.31 × 6501.55, **1-cent spread, size both sides**; tick 0.01 in 0.10–0.90, 0.001 in the tails | measured in the existing backtest |
| **Shadow status** | continuous (`l5-shadow-loop`) + paired A/B built | **not running** | **running continuously** |
| **Live status** | LIVE, $7.50/market, maker-only, conc 1, −$15 stop | none | DISARMED (`deploy/gemini_l5_probe/DISARMED.md`) |
| **Blocker** | execution: 4.52 fills/sequence vs L5's 32.48 | no collector, no service, no deploy config | needs the promotion ladder run, not new code |
| **Next gate** | forward cohort vs the frozen `L5_STATE_TRANSLATION_v1_PROMOTION_GATE` | build the collector + continuous shadow, then pre-register a Kalshi gate | replay → shadow → action-by-action → bounded probe |

## What the live Kalshi evidence actually says

**5-minute crypto does not exist.** Across all 274 series in Kalshi's Crypto
category the frequencies are `one_off` 59, `custom` 71, `annual` 51,
`hourly` 25, `monthly` 24, `daily` 22, **`fifteen_min` 14** — and no
`five_min` at all. So the answer to "current 5-minute and 15-minute
availability" is: 15-minute yes, 5-minute no. The 14 fifteen-minute series
are KXADA15M, KXBCH15M, KXBNB15M, **KXBTC15M**, KXCRYPTOCOMP15M,
KXCRYPTOLEAD15M, KXDOGE15M, **KXETH15M**, KXHYPE15M, KXNEAR15M, KXSOL15M,
KXTON15M, KXXRP15M, KXZEC15M.

**The clock is real, and that is the point.** `KXBTC15M-26SEP080800-00`
opened 11:45:00Z, closes 12:00:00Z, expects expiry 12:05:00Z, and settles
on a 60-second CF Benchmarks BRTI average. Seconds-to-settlement is a fact
here, not a translation — which is exactly why the sports event-progress
mapping must not be carried over. This lane keeps literal TTE.

**On maker fees, what is evidence and what is inference.** The venue's own
per-series metadata carries `fee_type`, and it distinguishes
`quadratic` (272 series) from `quadratic_with_maker_fees` (2 series:
KXBTCMAX125 and KXBTCMAX150, both long-dated customs). Both L5 lanes are
plain `quadratic` at `fee_multiplier: 1`. That the venue bothers to name a
maker-fee variant is strong evidence that plain `quadratic` series do not
charge makers — but **that last step is an inference from a field name, not
a published number, and no economics may rest on it until it is confirmed
against Kalshi's current fee schedule.** It is flagged rather than used.

If it holds, it matters: a maker-only L5 replication on a venue with a real
15-minute clock and no maker fee is structurally much closer to L5's native
economics than PMUS sports, where fees are `venue_unreported` and observed
pair costs run above par.

## The honest gap

Kalshi is the **least** ready of the three despite being the closest fit.
`src/kalshi_l5_shadow.py` is a one-shot analyser that reads a tape and
prints a status; it has no loop and makes no live call. The tape it expects
does not exist, because `src/policy_d_port/collector.py` — which the module
docstring points at — is a **Gemini** WebSocket collector, not a Kalshi one,
and it is not deployed either.

So "run a Kalshi shadow continuously" needs a collector that does not exist,
not merely a Dockerfile. The smallest safe build, reusing everything:

1. a keyless Kalshi poller for the live 15M crypto windows, appending the
   tape shape `kalshi_l5_shadow.load_tape` already reads;
2. a loop that runs the existing `shadow_status` over the rolling tape and
   emits `KALSHI_L5_SHADOW_STATUS`;
3. one Railway service reusing the `l5_shadow` deploy pattern — no new
   project, no duplicated recorder.

Gemini needs no new code: its shadow is already running. What it needs is
the promotion ladder actually run against it.

## Not claimed

- no live money on Kalshi or Gemini, and none is proposed here
- no PMUS risk scaling follows from this survey
- Kalshi book quality is one observation of one window, not a study
- Polymarket incentives and queue mechanics are NOT assumed to transfer
- designated/contracted market-maker programmes are out of scope: only
  economics available to an ordinary account are counted
