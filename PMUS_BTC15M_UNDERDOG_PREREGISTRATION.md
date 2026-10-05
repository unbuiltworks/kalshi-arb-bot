# PMUS BTC15m Underdog — Pre-Registration (Economics / Risk Framework)

**Freeze label:** `pmus-btc15m-underdog-v1`
**Freeze date:** 2026-10-05
**Status:** FROZEN BEFORE RESULTS. Outcome-blind.

This document and `src/pmus/btc15m_spec.py` are the pre-registration for the
PMUS BTC15m underdog **price × time** experiment. They were written without
inspecting any underdog outcome, any checkpoint calibration, any maximum
favorable excursion, any strategy P&L, or any 2026-10-01 tape.

Nothing here was tuned to data. Any later change is a **new, dated freeze**
and must be disclosed alongside this one; it does not retroactively become the
pre-registration.

**Scope discipline observed while writing this:** no tape was read, no live or
simulated orders were placed, no strategy was optimized, no Gemini path was
touched, and nothing belonging to the concurrent PMUS WS tape archiving work
(its recorder, Railway service, volume, deployment, or archive branch) was
inspected or modified.

---

## 0. Fee provenance (read this before trusting any number)

Every economic figure in this framework comes from **one** fee entry point:
`src/pmus/fees.py`. It resolves, in order:

1. `PMUS_FEE_IMPL=module:function` — bind the canonical PMUS fee
   implementation when it is importable;
2. `polymarket_us.fees.{taker,maker}_fee_cents`, if present;
3. an explicit **frozen fallback schedule**.

At freeze time **no PMUS fee module is importable in this repository.** `main`
carries only `src/fee_calc.py`, which is the *Kalshi* schedule and is
deliberately **not** used as a silent stand-in. The frozen fallback is
therefore the convex form `ceil(rate · C · P · (1−P))` with
`PMUS_TAKER_RATE` / `PMUS_MAKER_RATE` defaulting to **0.0**, matching
Polymarket US's published zero-trading-fee posture at freeze time.

Consequences, stated plainly:

- With a zero rate the fee-inclusive break-even win probability equals the
  executable ask in probability terms (e.g. 23c → 0.2300). Published tables
  below show exactly that, which is the correct degenerate case, not an error.
- Binding a non-zero rate, or binding the canonical implementation, changes
  only a **constant** — never the shape of any table, rule, or threshold here.
  Every table is regenerated from the same code, so re-binding is a one-command
  refresh.
- Every generated artifact stamps `fee_source` and a
  `fee_schedule_fingerprint`. **A table whose fingerprint does not match the
  fingerprint printed in the artifact is not the frozen table.** Before the
  holdout evaluation, the fee source MUST be confirmed (and re-stamped) against
  the canonical PMUS implementation.

**Open item, owned and visible:** bind the canonical PMUS fee implementation
via `PMUS_FEE_IMPL` and regenerate `PMUS_BTC15M_ECONOMICS.md` before any
candidate is evaluated on holdout.

---

## 1. Frozen checkpoints

Minutes remaining in the 15-minute window:

> **14, 12, 10, 8, 6, 5, 4, 3, 2, 1**

Ten checkpoints. Not to be added to, removed from, re-spaced or re-weighted
after outcomes are seen. A checkpoint observation is taken from the causal
executable book state at that minute mark — state at or before the mark only,
never a later revision.

## 2. Frozen executable-price buckets

Buckets are on the **executable ask** (what could actually be lifted), in
cents, half-open `[lo, hi)`:

> **<10c, 10–15c, 15–20c, 20–25c, 25–30c, 30–35c, 35–40c, 40–45c, 45–50c**

States where **neither** outcome has an executable ask strictly below 50c are
**not dropped and not merged into any bucket.** They are tallied separately as
`no-executable-underdog(>=50c both sides)`. This matters: discarding them
would inflate opportunity frequency and therefore every $/day figure.

The matrix is 10 checkpoints × 9 buckets = **90 cells**, plus the separate
no-underdog state. The 90-cell count drives the multiplicity correction in §5.

## 3. ≥ $5 NET sizing table

Generated into **`PMUS_BTC15M_ECONOMICS.md`** by
`python -m src.pmus.economics_report` (JSON via `--json`). Rule, for each
executable ask 1c…49c, in integer cents:

```
entry_fee = PMUS fee(q contracts @ ask, taker)
spend     = q · ask + entry_fee          # dollars actually risked
payout    = q · 100                      # settlement on a win
net_win   = payout − spend               # no PMUS settlement fee
loss      = spend                        # a loss is TOTAL
breakeven = spend / (spend + net_win)    # fee-inclusive win probability
```

`q` is the **smallest whole-contract quantity** with `net_win ≥ 500c`.

Constraints respected — **no infinite liquidity assumed**:

- whole contracts only, minimum 1, step 1 (no fractional sizing);
- self-imposed cap `MAX_CONTRACTS_PER_ENTRY = 1000`; an ask needing more is
  reported `INFEASIBLE`, never silently up-sized;
- entries are **taker** lifts of the causal executable ask;
- whole-cent ticks;
- **required displayed touch depth ≥ q** (`DEPTH_MULTIPLE_REQUIRED = 1.0`). An
  observation whose displayed touch depth does not cover `q` is **not** a
  qualifying opportunity. Size is never assumed to be available because a price
  was printed.

Over the whole 1c–49c domain the required size is small (single-digit to low
double-digit contracts), so the 1000-contract cap never binds and the depth
requirement — not the cap — is the real capacity constraint.

## 4. Risk mathematics

Also in `PMUS_BTC15M_ECONOMICS.md`:

- **Consecutive-loss table** — bankroll consequence of **5, 10, 15, 20**
  consecutive settlement losses at the frozen $5-net stake, for every entry
  price. Losses are total.
- **Expectancy grid** — expected P&L per trade across hypothetical **true**
  win probabilities (0.025 … 0.950 in 0.025 steps; a 0.05–0.50 slice is
  rendered in markdown, the full grid in JSON):
  `EV = p · net_win − (1 − p) · loss`.
  When empirical calibration arrives, read the observed win rate (and its
  interval) into this grid to get expectancy per trade immediately; multiply by
  observed opportunities/day for $/day.

**Staking rule, frozen: flat per price.** Stake is a function of the entry
price alone. **No Martingale, no anti-Martingale, no stake that depends in any
way on prior results**, realized P&L, drawdown state, or streak length.

## 5. Candidate-selection criteria — pre-registered, thresholds fixed now

A candidate **must not** be selected because one matrix cell looks profitable.
A single cell out of 90 clearing a bar is an expected artifact of noise.

All of the following are required, jointly:

| # | Criterion | Frozen threshold | Rationale (written blind) |
|---|---|---|---|
| 1 | Positive fee-adjusted expectancy | Observed win rate > fee-inclusive break-even for the cell's representative entry price | Break-even is the only economically meaningful null; a raw win rate says nothing. |
| 2 | Adequate observation count | ≥ **200 qualifying observations**, counted as **distinct 15-minute windows**, per cell (≥ **400** for a claimed region) | At p≈0.20, SE ≈ 2.8pp at N=200 — the smallest N that can resolve the 3pp effect required in #4. Observations at different checkpoints of the same window are **not** independent, so N counts windows, not rows. |
| 3 | Uncertainty explicitly reported | **Wilson 95% CI** on the win rate, plus a **day-level block bootstrap** (1000 resamples, resampling whole UTC days) CI on net $/trade. The Wilson **lower** bound must exceed break-even. | Point estimates are not evidence. Day-blocking respects the serial dependence of adjacent BTC windows. |
| 4 | Economically meaningful effect vs executable implied probability | Win rate ≥ break-even **+ 3.0 percentage points** *and* ≥ **1.15×** break-even (both) | An edge under 3pp is inside realistic slippage/latency/fee-binding error. The relative floor stops a 3pp bar being trivially cleared at low prices where break-even is itself ~10pp. |
| 5 | Multiplicity control | One-sided binomial test vs break-even across all 90 cells, **Benjamini–Hochberg FDR q = 0.10** | 90 cells at α=0.05 yields ~4–5 false positives by construction. FDR, not Bonferroni, because the cells are positively correlated and we want power. |
| 6 | Executable depth / capacity | Displayed touch depth covers the frozen size in ≥ **80%** of the cell's qualifying observations; median depth ≥ **2×** required size | Paper edge that cannot be filled at size is not edge. |
| 7 | Broad / neighboring region | The cell must sit in a region of ≥ **3** cells that are **adjacent in time or price**, each with a point estimate above break-even and at least one other also passing #3–#5 — **or** a written, pre-specified mechanism justification reviewed and recorded *before* holdout | Real microstructure effects are smooth across neighboring checkpoints and adjacent price buckets. An isolated cell is noise until a mechanism says otherwise. |
| 8 | Opportunity frequency | ≥ **2.0 qualifying opportunities/day** in discovery, and modelled net ≥ **$20/day** at the observed frequency | An edge that fires twice a month cannot matter in realized $/day, whatever its win rate. |

**No threshold above may be chosen, moved, or "clarified" by looking at
results.** They are fixed by this commit. If a threshold proves wrong, that is
a finding to report — not a number to adjust.

The frozen candidate rule that survives discovery must be **simple**: a
contiguous (checkpoint, price-bucket) region plus the frozen sizing rule.
No per-cell parameters, no fitted weights, no conditioning variables that are
not in this document.

## 6. Pre-registered clean validation

- **Quarantine.** Surviving executable tape for **2026-10-01, 07:00Z –
  23:xxZ** is HOLDOUT. During discovery nobody — human or agent — inspects its
  underdog outcomes, checkpoint calibration, maximum favorable excursion, or
  strategy P&L.
- **Discovery** uses eligible **2026-10-02 and later** tape only.
- **Disclosure ledger.** Any 2026-10-01 window touched by earlier calibration
  work must be listed in `PMUS_BTC15M_OCT1_DISCLOSURE.md` and **excluded** from
  the holdout evaluation. A search of this repository (all branches present
  here, all tracked markdown and source) found **no record of any 2026-10-01
  window having been inspected**. That is an absence of evidence in this tree
  only: before holdout is unsealed, the ledger must be completed from the
  concurrent archiving session's logs and from any out-of-repo calibration
  notes. **A ledger that has not been completed blocks the holdout run.**
- **One shot.** Only after a simple candidate rule is frozen in writing (a new
  dated commit) may it be evaluated **ONCE** on the eligible Oct. 1 holdout.
  One rule, one evaluation, result reported whatever it is. A second look at
  holdout — for any reason, including "fixing" the rule — destroys it, and the
  holdout is then spent and must be declared spent.

## 7. The money report (definition, frozen)

Produced **separately** for discovery and for holdout, never pooled:

1. qualifying opportunities (count)
2. opportunities/day
3. wins / losses
4. win %
5. average executable entry price
6. dollars risked
7. gross settlement P&L
8. fees
9. net realized-model P&L
10. net $/trade
11. net $/96-window day at the observed opportunity frequency
12. maximum consecutive losses
13. maximum drawdown
14. largest individual stake
15. touch-depth / capacity constraints (how often depth bound the size, and the
    size actually achievable)

Each report also carries the §5 uncertainty intervals and the
`fee_schedule_fingerprint`.

**Comparison with the historical $480/day target is reported explicitly and
without assuming it is achievable.** The target is a yardstick, not a
hypothesis — "realized model $/day vs $480/day" is stated as a ratio with its
interval, and a result far below it is a result, not a reason to re-tune.
`net $/96-window day` is "realized-model" throughout: settlement-held,
fee-inclusive, no exit path assumed.

## 8. Future experiment — DESIGN ONLY, NOT RUN

Specified here and **deliberately not executed**; its data paths are
**preserved uninspected** for later testing.

> **Early-exit test.** Buy a qualifying underdog at the causal executable ask
> (§3 sizing). Then determine whether the *subsequent causal executable
> BID and its depth* permit an exit producing **≥ $5 net** before settlement.

Design, frozen now so it cannot be tuned later:

- **Causality.** Only book states strictly after the entry timestamp count. No
  bid may be used that was not displayed, at depth, after entry.
- **Exit test.** At each subsequent book update until window close: exit
  proceeds `= q · bid − exit_fee(q, bid, taker)`; the exit qualifies iff
  `proceeds − spend ≥ 500c` **and** displayed bid depth ≥ `q`.
- **Outcome measures.** Share of entries with any qualifying exit; time to
  first qualifying exit; exit P&L vs settlement P&L on the *same* entries;
  how often an exit path existed on entries that went on to lose.
- **Blinding.** Maximum favorable excursion and all bid-side paths for the
  quarantined Oct. 1 tape stay sealed through both discovery and the §6
  holdout run.
- **Separate pre-registration.** This experiment gets its own dated freeze
  before it is run. It is **not** licensed by this document.

---

## Artifacts

| Path | Contents |
|---|---|
| `PMUS_BTC15M_UNDERDOG_PREREGISTRATION.md` | this document |
| `PMUS_BTC15M_ECONOMICS.md` | generated sizing / loss-streak / expectancy tables |
| `PMUS_BTC15M_OCT1_DISCLOSURE.md` | Oct. 1 inspection ledger (must be completed before holdout) |
| `src/pmus/fees.py` | single canonical PMUS fee entry point + fingerprint |
| `src/pmus/btc15m_spec.py` | frozen checkpoints, buckets, sizing, risk math |
| `src/pmus/economics_report.py` | table generator (markdown / JSON) |
| `tests/test_pmus_btc15m_spec.py` | guards that fail loudly on post-hoc edits |
