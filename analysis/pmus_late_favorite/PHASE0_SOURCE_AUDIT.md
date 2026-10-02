# PMUS BTC15m late-favorite / opposite-longshot — Phase 0 source & provenance audit

**SHADOW / HISTORICAL ONLY. No order was placed, prepared, previewed or cancelled.
No executor was built. No production service, variable or deployment was changed.**

Lane opened 2026-10-02. This lane is **separate** from, and shares no dataset,
threshold, conclusion or validation window with:

1. the active PMUS passive-maker / toxicity experiment
   (`claude/l5-pmus-economic-results-7c116e`, and the production recorder on
   `feature/l5-transfer-backtest`);
2. the parallel Gemini lane (untouched — no file under `analysis/gemini/`,
   `deploy/gemini_*` or `data/transfer_backtest/gemini_*` is read or written here);
3. the faithful-L5 live-probe lane (`claude/polymarket-us-btc15m-l5-0l1xw9`).

## Verified current state — checked, not assumed

| claim as given | verified | evidence |
|---|---|---|
| canonical repo `unbuiltworks/kalshi-arb-bot` | **yes** | both research lanes and the recorder service build from it |
| production PMUS recorder on `feature/l5-transfer-backtest` at `ddc2195c` | **yes** | `pmus-ws-evidence-recorder` `71b6ea99`, source branch `feature/l5-transfer-backtest`; branch head `ddc2195cb83df1f51f182f2ae583d6a0da789edf`, authored 2026-10-02T17:12:46Z; deployment `ed464f2d` SUCCESS created 2026-10-02T17:12:51Z; volume `/state/pmus-ws` (50 GB) mounted |
| `ddc2195c` is the ordered seen-retention fix for BOTH book and trade streams | **yes** | commit message and `tests/test_pmus_seen_retention.py` (13 tests) cover `emit_new_books` and `emit_new_trades`; the bug retained 2,000 *arbitrary* identities from an unordered `set`, so dropped identities caused already-emitted rows to re-emit and clip the cap |
| `btc-ref-collector` is separate and already running | **yes** | service `6cf313d3`, state `live`, built from `claude/l5-pmus-economic-results-7c116e`, deployment `a6b10767` SUCCESS 2026-10-02T12:56:16Z. **Not touched by this lane.** |
| `scripts/pmus_railway_log_to_tape.py` deduplicates harvested PMUS data | **yes, with a correction** | dedupe key is `(slug, t, px/bid, qty/ask)`. **The script is NOT on the production recorder branch** `ddc2195c`; it exists only on `claude/l5-pmus-economic-results-7c116e`. |
| `src/pmus_fees.py` is the authoritative effective-dated fee/rebate implementation | **yes** | byte-identical on both branches (blob `de25f91`). Taker θ 0.06 → **0.0695** effective 2026-09-17T04:00Z; maker θ **0.0125 paid as a rebate at the point of trade**; `fee = θ·C·p·(1−p)`, banker's rounding to the cent; reproduces all 99 published price rows. Every window in this lane post-dates the θ change, so taker 0.0695 / maker −0.0125 apply throughout. |
| the active experiment's first clean post-fix windows are RESERVED | **yes, and they are the only post-fix windows that exist** | the fix landed 17:12Z *today*; every window with usable late-window tape pre-dates it |

### Instrument facts (venue-published, re-read rather than recalled)

PMUS series 375 `btc-updown-15m`, market `cpc-btc-updown-15m-YYYY-MM-DD-HHMMz`,
one market per window, binary, $1.00 notional to the winner, tick 0.01,
price 0.01–0.99, settlement source CF Benchmarks **BRTI**.

**This is an UP/DOWN market, not a struck market.** It settles Up iff the BRTI
value at `T_end` is **greater than or equal to** the value at `T_start`, each
being the mean of 60 one-second BRTI prices in the preceding minute, rounded to
2 dp. Therefore:

> **the "strike" is the window's own opening reference, `P0`, and the tie goes to Up.**

Every distance quantity in this lane is measured against that `P0`, not against
a listed strike price. Any statement that imports a struck-market intuition is
wrong for this instrument.

## Source / provenance table

| need | source | provenance class | cadence / span | limitation that bounds the study |
|---|---|---|---|---|
| PMUS synchronized book states | `data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz`, 26,143 `book` rows | authenticated WS, recorder log harvest | ~0.2 s p95, 8 slugs, 2026-09-30 12:15z–13:45z + a 45 s fragment 2026-10-02 04:00z | top-of-book only: `bid/ask/bid_qty/ask_qty`. **No depth beyond the touch**, so a quote behind the touch has unknown queue and must never be filled |
| PMUS authenticated trades | same file, 16,620 `trade` rows | authenticated WS | 381–3,046 trades/window | `dir` is the **taker** direction and `bid_at_trade`/`ask_at_trade` is the touch bound by the recorder at the instant of the trade, so trade→book sync needs no merge and carries no clock skew |
| PMUS book states (wider span) | `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl` (3,298 rows) + `raw_railway_logs/2026-09-28/*.gz` (13 hourly slices) | public gateway, read-only, no credentials | ~10 s, 2026-09-27 18:27z → 2026-09-28 14:xx z | `bbo` + full offer ladder, **but no trade tape** (public `/trades` is 404). Supports taker-side calibration only; cannot establish trade-through |
| BTC reference price | `data/btc15m/coinbase_1m.jsonl.gz`, 194,478 1-minute candles | Coinbase spot, public | 1 min, 2026-05-20 → 2026-10-02 | **PROXY, NOT SETTLEMENT.** BRTI and Chainlink are unreachable from this container; Binance is HTTP 451. A 1-minute OHLC bar cannot reproduce a 60-second mean, so `P0`/final are approximated by OHLC4 of the first/last minute. **Worst exactly where this study is most sensitive: near-tie windows and the tie-goes-Up rule.** |
| strike (= `P0`) | derived from the window's own opening minute | derived | — | inherits the proxy error above |
| window start / end | parsed from the slug (`:00/:15/:30/:45`); venue `endDate` = start + 15 min + ~10 s | venue catalogue | exact | the clock is literal, no translation |
| settlement outcome | derived: Up iff final ≥ `P0` (tie → Up) | **derived from the proxy** | — | **not a venue settlement record.** Terminal book convergence is available as an independent cross-check on the 6 authenticated windows and will be used as such |
| fee / rebate schedule | `src/pmus_fees.py` | venue-published, effective-dated, 99/99 rows reproduced | — | taker rebate tier starts at $250k prior-month notional → **counted at zero**, not assumed available |
| queue / depth evidence | `bid_qty`/`ask_qty` at the touch + the authenticated trade tape | authenticated WS | 6 windows only | queue-ahead at the touch is observable; **anything behind the touch is unknown**. Maker arms are therefore restricted to the 6 authenticated windows |

### Deduplication is provably lossless on the committed tape

Required before any pre-fix tape may be used. Measured on
`pmus_btc15m_auth_tape.jsonl.gz`:

| check | result |
|---|---|
| book rows raw / unique on `(slug,t,bid,ask,bid_qty,ask_qty)` | 26,143 / **26,143** — 0 duplicates |
| trade rows raw / unique on `(slug,t,px,qty,dir)` | 16,620 / **16,620** — 0 duplicates |
| multiplicity distribution | every key appears exactly once |
| `(slug,t)` pairs carrying >1 **distinct** book state | **0** |

The last row is the one that matters: because no timestamp carries two different
states, timestamp-keyed dedup cannot merge two genuine observations. The harvest
is already deduplicated and no unique observation was destroyed.

**This does not clear the pre-fix tape for proof.** The seen-retention bug's
harm was *censoring*, not duplication-after-dedup: duplicate rows consumed the
per-emission cap, so genuinely new rows could be clipped and never reach the log
at all. Dedup cannot recover a row that was never emitted. Residual censoring is
visible as gaps — p95 inter-observation gap is 0.1–0.7 s but the **maximum** gap
is 5.2–10.5 s on every window. Accordingly **all pre-fix windows are capped at
ENGINEERING/DISCOVERY** and none is admitted as proof.

## Eligible-window classification

Frozen in `analysis/pmus_late_favorite/windows.json`, built by
`scripts/pmus_late_fav_windows.py` from **provenance and coverage only** — no
price, outcome, settlement or P&L quantity is read by the classifier, so the
split cannot have been tuned.

A window is counted only if the tape covers **all five** pre-registered
checkpoints T-300, T-180, T-120, T-60, T-30.

| class | n | which |
|---|---|---|
| `ENGINEERING/DISCOVERY` | **38** | 32 public-gateway windows 2026-09-27 18:30z → 2026-09-28 02:15z, plus **6** authenticated windows 2026-09-30 12:15z–13:30z (these 6 are the only ones with a trade tape) |
| `RESERVED_OTHER_EXPERIMENT` | **51** | present in `data/pmus_btc15m_tape_oos/` — the active passive-maker/toxicity experiment's reserved sample, 2026-09-28 02:30z–15:00z. **Not read, not scored, not consumed by this lane.** |
| `RECORDER_VALIDATION_ONLY` | 0 *(see below)* | no window recorded at/after `ddc2195c` yet has usable late-window tape; the fix is ~25 minutes old |
| `CANDIDATE_VALIDATION` | **0** | empty by construction — see the data gap |
| `UNUSABLE` | 3 | `2026-09-30-1345z` (tape ends T+9 min, so T-300 is never observed), `2026-10-02-0400z` (45 s fragment), and one further uncovered slug |

Honouring the reservation is what costs this lane most: of 82 public-tape
windows with late coverage, **50 fall inside the other experiment's OOS sample**
and are excluded, leaving 32.

## The data gap that currently prevents a valid study

Stated plainly, because it governs everything downstream:

1. **No validation sample exists, and none can be manufactured.** Every window
   with usable late-window coverage is pre-`ddc2195c`; the only post-fix windows
   belong to the active recorder experiment. `CANDIDATE_VALIDATION` is therefore
   empty *by construction*, not by oversight. Any rule this lane proposes must be
   frozen now and scored on windows that **do not yet exist**.
2. **38 independent markets cannot populate the pre-registered grid.** 5 time
   checkpoints × 5 `|z|` buckets = 25 cells. Even pooling all 38 markets across
   all 5 checkpoints gives 190 *non-independent* observations (5 per market), and
   the late-window states within one market are strongly serially dependent. The
   effective n per cell is ~1. A favorite settlement rate measured on 6–8 markets
   has a 95% interval roughly ±35 points wide — wider than any edge worth trading.
3. **Maker arms (B and C) are restricted to 6 markets.** Trade-through evidence
   requires the authenticated trade tape, which exists for 6 windows on a single
   day, 2026-09-30 12:15z–13:30z — one 90-minute stretch, one BTC regime. Fill
   rate, time-to-fill and adverse selection cannot be estimated from that, and a
   conditional-on-fill probability from 6 windows is not a measurement.
4. **Settlement truth is a 1-minute proxy, not BRTI.** The settlement rule is a
   60-second mean and the tie goes to Up; a 1-minute OHLC bar cannot reproduce
   either. The error concentrates in exactly the near-tie windows, so the
   lowest-`|z|` bucket — where miscalibration would be most interesting — is the
   least trustworthy. The 6 authenticated windows admit a terminal-book
   cross-check; the 32 public windows largely do not.
5. **A capacity claim is not reachable from the touch alone.** The authenticated
   tape carries no depth beyond the touch, so the maximum size for which all
   fills remain supported by observable liquidity can be stated only at the
   touch. The public ladder gives depth but no trades. $3 and $5 per window at
   realistic size cannot be settled on this sample, and q1 results must not be
   scaled linearly.

### What this lane can honestly do now

- Freeze the state definition, checkpoints, buckets and window IDs **before**
  any outcome is joined — done in `PREREGISTRATION.md` and `windows.json`.
- Report the Phase-1 calibration residual on the 38 discovery markets **with
  intervals**, as a *direction-of-effect reading only*, explicitly not as
  evidence for or against a strategy.
- Build and invariant-test the execution machinery (A/B/C/WAIT) against the 6
  authenticated windows, labelled engineering.
- Answer the L5 behavioural question (Phase 4) on canonical L5 history, which is
  independent of PMUS sample size.
- Specify the forward accrual needed to make the question answerable.

Nothing in this lane promotes a rule, and no number produced here may be quoted
as realized, expected or validated profit.
