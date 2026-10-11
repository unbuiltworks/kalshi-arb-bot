# BTC15m execution and profitability audit

**Read-only / shadow.** No order was sent, no gate was bypassed, no capital was
committed, no service was created or changed. Nothing below is submitted,
filled, settled or realized. Rebates are counterfactual throughout and are
never added into a NET line.

Date: 2026-10-11. Branch: `claude/btc15m-execution-audit-jjdxce`.
Venue: Gemini prediction markets, `GEMI-BTC15M*-UP`.

---

## 0. What exists, and what is canonical

| artifact | where | state |
|---|---|---|
| canonical BTC15m continuous-inventory engine | `ci15m-exec-1` @ `730318b7`, `analysis/gemini/info_edge/btc15m_shadow_policy.py` (+ `src/gemini_execution/ci15m_*`) | 2026-10-03, shadow; imports `classify` and `fi_core.Inventory` — one engine, not two |
| two-sided BTC15m order-book + print tape | `research/gemini-btc5m-information-edge` @ `ea66d19e`, `analysis/gemini/btc15m_rec/*.jsonl.gz` | 2026-10-06, 19 segments, 16 validated, ~5.3 h |
| live maker cohorts | `ci15m-exec-1`, `analysis/gemini/cohorts/*BTC15M*`, `EVIDENCE_PACKET_016_COHORT1.md` | 2026-09-21/22, SPENT, DISARMED |
| directional execution study | `analysis/gemini/info_edge/R2_EXECUTION_RESEARCH.md` | 2026-10-03 |

Nothing was rebuilt. This audit adds three read-only analysis modules beside
the existing ones and reuses the engine's own book reconstruction discipline,
its risk constants, and its fee/rebate arithmetic.

### A venue fact that reframes the whole hypothesis

There is **no separate `-DOWN` instrument**. The recorder probed for one and
the venue answered `-1013 Invalid stream name` on every attempt (96 probe rows,
0 successes). The DOWN book is derived:

```
DOWN bid = 1 - UP ask        DOWN ask = 1 - UP bid
```

So "rest UP and DOWN maker orders simultaneously at the best bid" **is** "rest
an UP bid at the touch and an UP offer at the touch" — one two-sided quote in
one book. A completed pair costs `UP_bid + (1 - UP_ask) = 1 - spread`, so the
edge being reached for is the single-leg spread, median **$0.11** on this tape.
This is a market-making hypothesis, not a two-instrument arbitrage.

---

## 1. Were the original 10-window tests evaluated correctly?

**No. Six of ten windows ran, four were scored, and the stop that ended the
cohort fired on a number that was not a loss.** This is established from
`EVIDENCE_PACKET_016_COHORT1.md` and the on-disk cohort ledgers, not from
narrative.

Ledger state of `COHORT-2026-09-21-BTC15M-COHORT1-W01..W10`:

| window | ledger rows | content |
|---|---|---|
| W01 | 12 | **12 × `TOMBSTONE_UNRECOVERABLE`** — the runner exited 1 on an authorized stop and its evidence commit was skipped; two windows' trades are gone and are not reconstructed |
| W02, W03 | 30 each | 12 `PLACE` + 15 `RESOLVE/ACCEPTED` + 3 `CLEANUP_CANCEL` |
| W04–W10 | **0 each** | never dispatched |

Four defects, in order of how badly they corrupted the read:

1. **The aggregate stop measured turnover, not loss.** `window_loss_usd`
   summed cash out of the account from confirmed BUY fills with no settlement
   credit and no matched-pair offset. For a strategy that buys both legs and is
   paid $1.00 on one, that accumulates roughly half of turnover as "loss"
   whether the cohort is winning or losing. It halted an authorized cohort at
   10.63 of a 10.00 cap **while the recorded windows were net positive**. Fixed
   2026-09-22 in `src/gemini_execution/cohort_economics.py`, which now keeps
   seven lines apart and lets only `cumulative_economic_loss_usd` (settled
   windows only) drive the stop.
2. **Slot theft.** `ledger_for` returned the first *unspent* ledger, so window
   4 inherited W02's leftover 4 slots and window 6 inherited W03's 5. Two of
   six windows never received the authorized 12 actions and stopped with "no
   mutation slots left to cancel with". Fixed.
3. **Evidence loss on an authorized stop.** The evidence commit was conditioned
   on job success, so an authorized stop produced a red job and no artifacts.
   Fixed (`always()` commit; authorized stops exit 0).
4. **Settlement accounting was absent, not negative** — see §3.

**Corrected economics of the four preserved windows** (venue-confirmed fills,
venue-confirmed fees, public resolution):

| contract | side | yes | no | spend | fees | payout | settlement-implied net |
|---|---|---:|---:|---:|---:|---:|---:|
| …2609220230-UP | yes | 3 | 4 | 2.52 | 0.00 | 3.00 | **+0.48** |
| …2609220245-UP | yes | 2 | 0 | 0.71 | 0.00 | 2.00 | **+1.29** |
| …2609220300-UP | no | 4 | 2 | 2.40 | 0.00 | 2.00 | −0.40 |
| …2609220315-UP | no | 2 | 1 | 1.23 | 0.00 | 1.00 | −0.23 |
| **total** | | | | **6.86** | **0.00** | **8.00** | **+1.14** |

All **18 fills were maker**, every one carrying the venue's own `n` field at
`0.0000` — a confirmed zero, not an absent one. `fees_missing: 0`.

So the answer to "did the 10-window test show negative economics" is: it never
produced ten windows, its four scored windows were **positive**, and the thing
that stopped it was a mis-named turnover cap. The honest verdict on that cohort
is **n=4, +1.14 settlement-implied, no expectancy claim available** — not a
negative result.

`COHORT-2026-09-22-...-COHORT2-W01..W20` ran **11 of 20**; its ledgers are
mutation records only (PLACE/CANCEL/RESOLVE with price and leg, no fill or
settlement rows), so no economics can be read from them in-repo.

---

## 2. Does the bid-based approach improve net BTC15m economics?

**Not established, and on the only tape that can currently be replayed it does
not: 0 completed pairs in 26 windows.** But that tape is blind to 58% of the
window (§4), so this is a *failure to measure*, not a refutation.

Method, on the 26 settlement-resolved BTC15m windows of 2026-10-06
(`analysis/l5/btc15m_execution_audit/maker_arms.py`):

* book rebuilt from `p_snapshot` + `p_depth` with the engine's own discipline
  (no diff before a snapshot, no diff whose `u` fails to advance);
* **no touch=fill.** A resting order fills only on venue print evidence, with
  the aggressor read from the venue's own `buyer_is_maker` flag:
  * `THROUGH` — an aggressive print strictly through our price; price-time
    priority cannot leave us behind it;
  * `DEPLETION` — aggressive prints *at* our price whose cumulative quantity
    since placement exceeds the size displayed at our price when we placed
    (an upper bound on our queue position);
  * `OPTIMISTIC` — any same-price print. Reported as a bound, **never counted**;
* a re-peg resets queue position and depletion credit — that is the measured
  cost of repricing;
* LOT = 2 per leg, TTE band [120, 840], `MAX_PAIR_COST 0.96`, `MAX_RESIDUAL 6`,
  `MAX_FILLS 8` — the canonical engine's own constants;
* fee/rebate arithmetic **verified identical** to `fi_core` across
  C ∈ {1, 2, 10, 100, 23.61} × P ∈ 0.01…0.99 (`--verify-fi-core`).

| arm | quoting obs | placements | evidenced fills | **matched contracts** | both-leg windows | fees | residual $ | **NET $** | net/window | bootstrap P(mean>0) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **A** canonical engine, marketable limit (EXTEND) | — | — | 60 | **30** | — | 1.83 | −6.68 | **−6.41** | −0.247 | 0.190 |
| **A** canonical engine (NO_EXTEND) | — | — | 34 | **20** | — | 1.01 | −4.38 | **−4.29** | −0.165 | **0.051** |
| **B** two-sided maker AT the touch, re-pegged | 4797 | 3460 | 6 | **0** | **0/26** | 0.06 | +0.04 | −0.02 | −0.0008 | 0.476 |
| **C** two-sided maker ONE TICK behind, re-pegged | 4797 | 3415 | 4 | **0** | **0/26** | 0.04 | −0.54 | −0.58 | −0.0223 | 0.330 |
| **D1** B's prices, HELD (queue priority preserved) | 4797 | **53** | 8 | **0** | **0/26** | 0.08 | +2.22 | +2.14 | +0.0823 | 0.861 |
| **D2** B's prices, re-pegged, inventory-aware | 4797 | 3397 | 6 | **0** | **0/26** | 0.06 | +0.04 | −0.02 | −0.0008 | 0.476 |

Every bootstrap interval for B/C/D1/D2 straddles zero. **Nothing here is
distinguishable from no trading at all.** D1's +2.14 is the sign of seven coin
flips, not an edge, and its own fill prices say so: it was filled at UP 0.10,
UP 0.50 and DOWN 0.80 — stale quotes reached only because price ran through
them.

Three things in that table are real findings rather than noise:

* **Completed-pair rate is 0/26 in every maker arm, against 30 matched
  contracts for the taker engine on the identical windows.** Both maker legs
  never filled in the same window once. The matched-pair spread capture the
  hypothesis depends on did not occur at all — and it is not that pair
  completion is impossible on these windows, because arm A completes pairs
  routinely by crossing. It is specifically the *passive* second leg that
  never arrives. Arm A's matched book is profitable (+$2.10 matched P&L); it
  loses −$6.68 on the directional residual it cannot complete, which is the
  same residual problem the maker arms have in a more expensive form.
* **Queue priority is worth far more than repricing, in order traffic.** D1 got
  *more* fills (8 vs 6) from **53 placements against 3,460** — 1.5% of the
  traffic. Re-pegging to the touch 3,400 times bought nothing, because the
  binding constraint is counterparty flow, not price.
* **Inventory-aware repricing (D2) is indistinguishable from naive (B)**:
  identical fills, identical net. With no second leg ever filling, there is no
  inventory for inventory-awareness to manage.

Independent, non-overlapping evidence on the *directional* version of the same
question — `R2_EXECUTION_RESEARCH.md`, 65 opportunities over 42 windows, same
THROUGH/DEPLETION standard: resting at the best bid filled 9.2% of the time and
those fills were correct **16.7%** of the time against 86.2% unconditionally, a
69-point accuracy collapse that a 33¢ price advantage does not cover. Maker at
best bid: EV **−0.0234/opp**; immediate taker: **+0.0377/opp**. That study is
about resting on the favoured side of a signal, so it does not settle the
two-sided question — but it is the only measured maker-vs-taker comparison in
the programme and it points the same way.

---

## 3. Actual evidence supporting maker fills

Maker fills on BTC15m are **proven to exist** — by live orders, not by model:

* **18 maker fills across 4 live windows**, 2026-09-22, every one with the
  venue's own fee field at `0.0000`, and the venue's trade record carrying
  `aggressor: false`. Both legs filled in **3 of those 4 windows**
  (matched pairs 3, 0, 2, 1 → 6 matched pairs). Entry TTEs: **865.8, 867.8,
  870.5, 879.2 s.**
* `CONTINUOUS_8_RESULT.md`: a single maker fill (`YES 0.01 @ 0.31`, aggressor
  false, fee 0) on a 5-minute contract, with every rung posted
  `maker-or-cancel` — a MOC that would cross is rejected, so the venue's own
  acks prove the rungs were strictly passive.

Against that, the October shadow tape yields **4–8 evidenced fills across 26
windows** (0.15–0.31 per window) versus the September live rate of **4.5 per
window**, a 15–30× gap. Almost every evidenced fill is `THROUGH` — i.e. the
quote was reached only when price moved through it.

The two numbers are reconcilable, and the reconciliation is §4: the September
fills were obtained at TTE 865–879, and **the October tape contains no
observation above TTE 420.**

---

## 4. The single highest-impact verified defect

> **`SUBSCRIBE_LEAD_S = 420.0` is inherited unchanged from the 5-minute
> recorder, so 58.3% of every BTC15m quoting band — the entire early window,
> TTE 840 → 420 — was never recorded.**

`src/gemini_research/btc15m_recorder.py` imports `book_task` from
`src/gemini_research/btc5m_recorder.py`, which subscribes depth and trade
streams only for markets inside
`[expiry − SUBSCRIBE_LEAD_S, expiry + RETAIN_AFTER_S]`. 420 s of lead fits a
300 s window. A BTC15m window is **900 s**.

Measured, not inferred (`coverage_audit.py`, gate G1 → `FAIL_EARLY_WINDOW_ABSENT`):

```
policy quoting band            TTE [120, 840]   = 720 s
max observed TTE               419.9 s
book events                    15,149   of which above 420 s:  0
venue prints                        51   of which above 420 s:  0
band seconds observed          299.9  ->  41.65% of the band
unobserved                     TTE 840 -> 420   (58.35%)
```

Zero of 15,149 book events and zero of 51 prints exist above the 420 s wall.
This is absence, not thinness.

It lands exactly where it does the most damage. The only real two-sided maker
completion this programme has ever obtained came from windows entered at
**TTE 865–879** — inside the unrecorded region. So the 0/26 completed-pair
result in §2 is measured over the late 42% of the window and **cannot refute
the hypothesis**; it can only say that late-window two-sided quoting does not
complete pairs, which it says clearly.

Two further verified defects, both of which also suppressed valid readings:

* **G3 — the settlement reader fails open.** Two tape producers disagree:
  `ie_collector` writes `resolution_side` / `settlement_value` / `strike`; the
  two-sided recorder writes `settlement.value` + `raw_event` and **no**
  `resolution_side`. `btc15m_shadow_policy.shadow_window` does
  `res = settled.get("resolution_side")` and, when it is absent, emits **no
  `settlement` block at all** — so every economic column printed `-` and the
  cohort total printed **0**, indistinguishable from "the policy made no
  money". Running the unchanged engine on the Oct-6 tape produced
  `resolution=None` on all 24 windows and `TOTAL 0`. With settlement bridged
  (`settlement_bridge.py`, UP wins on `value >= strike`, ties to UP, derivation
  validated against the venue's own `resolution_side` on **58/58** comparable
  windows with **0 disagreements**), the same unchanged engine on the same tape
  reports **−6.41 (EXTEND) / −4.29 (NO_EXTEND)**. The defect converted a
  decisively negative result into a blank.
* **G2 — the print tape is incomplete.** 51 captured `p_trade` rows against 85
  REST `lastTradePrice` changes in the same markets over the same streamed
  intervals — a capture ratio of **≤0.60**, and `lastTradePrice` changes are
  themselves a lower bound on trades. Every evidenced-fill count above is a
  floor.

---

## 5. Per-window reconstruction: wanted / did / blocked by / economics

Pattern across all 26 windows (full detail in
`results/maker_arms_20261006_tape.json` and
`results/canonical_engine_20261006_bridged.json`):

| | |
|---|---|
| **wanted** | quote both legs continuously inside TTE [120, 840]: UP bid at touch, UP offer at touch (= DOWN bid), LOT 2, pair cost ≤ 0.96 |
| **did** | quoted on 4,797 book observations, placed/re-pegged 3,400+ times per re-pegging arm; never both legs filled in one window |
| **prevented by**, in order | (i) **no observation at all above TTE 420** — 58% of the band absent; (ii) **no counterparty print at our price** in the observed 42% — ~2 prints per window per market, print tape itself ≤60% complete; (iii) **top-of-book size is typically 400 contracts**, so a 2-lot sits behind 400 and reaches the front only when price runs through it, which is the adverse case; (iv) `MIN_SPREAD_TICKS`/coherence gates correctly idle on `ONE_SIDED`/`EMPTY` books, which are **65–75% of all polls** (TWO_SIDED 234–480 of 800–1,000 per segment) |
| **economics under defensible fills** | matched-pair P&L **$0.00** in every maker arm; all P&L is single-leg directional residual; totals −0.58 … +2.14 over 26 windows, every bootstrap CI straddling zero |

No safety gate was found suppressing a valid trade. The gates that fired —
coherence, spread, residual cap, TTE floor — fired on books that genuinely
could not support the quote. The one gate that *did* suppress a valid result
was the cohort-1 turnover-cap-named-loss-cap, and it was fixed in September.
Execution did not diverge from intended policy on the October tape: the engine
quoted where it said it would, and the venue did not trade with it.

---

## 6. Net profit per window after costs

All shadow or settlement-implied. **Nothing is realized.**

| population | n windows | net | per window | note |
|---|---:|---:|---:|---|
| live maker cohort, 2026-09-22 | 4 | **+$1.14** | +$0.285 | settlement-implied from venue-confirmed fills; 18 maker fills, fees $0.00 confirmed; 2 further windows unrecoverable |
| canonical engine, 2026-10-03 tape | 7 | +$5.02 | +$0.717 | **+$4.80 of it is one window's directional residual** |
| canonical engine, 2026-10-06 tape (settlement bridged) | 26 | **−$6.41** EXTEND / **−$4.29** NO_EXTEND | −$0.247 / −$0.165 | previously read as 0; NO_EXTEND bootstrap P(mean>0) = 0.051 |
| maker arm B, at the touch | 26 | −$0.02 | −$0.0008 | 0 completed pairs |
| maker arm C, one tick behind | 26 | −$0.58 | −$0.0223 | 0 completed pairs |
| maker arm D1, held | 26 | +$2.14 | +$0.0823 | 0 completed pairs; 7 coin flips |
| maker arm D2, inventory-aware | 26 | −$0.02 | −$0.0008 | 0 completed pairs |

Rebates: **$0.01 in total** across every maker arm, and the R2 study found
**$0.000** eligible across all five of its policies. At q=1–2 the
`floor(0.021·C·P·(1−P))` rebate floor-rounds to zero almost everywhere, so no
version of this strategy is rescued by rebates at probe size. No rebate is
called realized.

Pooling the two engine tapes gives 33 windows and **−$1.39** — dominated by two
single-window residual outcomes of opposite sign (+4.80, −3.42). There is no
window count here that supports an expectancy claim in either direction.

---

## 7. The smallest concrete fix

**One constant, and one three-line fail-closed guard.** In priority order:

1. **Record the early window.** In `src/gemini_research/btc15m_recorder.py`
   (branch `research/gemini-btc5m-information-edge`), override the inherited
   `SUBSCRIBE_LEAD_S` to **≥ 960 s** for the 15m series. The value is imported
   from `btc5m_recorder`, so the 15m module must set its own rather than
   inherit. Then add a validator gate asserting
   `max observed TTE ≥ OPEN_TTE_HI (840)` so the next gap **fails closed**
   instead of producing a confident 0/26.
   *Cost:* one constant and one assertion; the existing recorder workflow
   re-run unchanged. No new service, no new branch, no new strategy.
2. **Fail closed on settlement.** In `btc15m_shadow_policy.shadow_window`,
   replace the silent `settled.get("resolution_side")` with
   `settlement_bridge.resolve()` and refuse to emit a cohort total that
   includes an unresolved window. This audit's bridge is validated 58/58
   against the venue's own field and is already written.
3. **Then re-run the four arms unchanged** on a tape that covers TTE 840→120,
   with the *same* module in this directory. The gate is a single number:
   **completed-pair rate > 0 with evidenced (THROUGH/DEPLETION) fills on both
   legs in the same window.** It is 0/26 today and must stop being 0 before any
   profitability question is even answerable.

Everything else can wait. Fixing the print-capture shortfall (G2) only
tightens a bound; fixing the coverage wall (G1) is what makes the hypothesis
testable at all.

---

## 8. Is the strategy ready for bounded live validation?

**No.** Not blocked on authorization — blocked on measurement.

| requirement | state |
|---|---|
| maker fills are reachable on BTC15m | **YES** — 18 live maker fills, venue-confirmed, fee 0.0000 |
| both legs complete in one window | **demonstrated live 3/4 windows (Sept, early entry); 0/26 in shadow (Oct, late window only)** |
| the deciding evidence exists | **NO** — 58.3% of every quoting band is unrecorded |
| net economics are positive | **NOT ESTABLISHED** — every population's CI straddles zero; the best-powered one (n=26, bridged) leans negative |
| rebates would rescue it | **NO** — $0.01 measured, $0.000 in R2; floor-rounds to zero at probe size |
| safety gates are suppressing valid trades | **NO** — the one that did was fixed in September |

The programme is **two cheap read-only steps** from a decidable question, and
those steps are §7.1 and §7.2. A bounded live probe now would spend a mandate
to re-learn what a fixed recorder constant can tell us for free, on a
hypothesis whose central mechanism — a completed two-sided pair — has never
been observed in any shadow replay.

Recommended next action, and nothing beyond it: apply §7.1, re-run the existing
recorder workflow for one overnight session, re-run `maker_arms.py` on the
result, and report the completed-pair rate. If it is still 0, the resting-bid
hypothesis is dead on Gemini BTC15m at this size and the lane should close. If
it is positive, that is the first defensible basis for asking for a bounded
live mandate.

---

## Reproduction

From a tree containing the canonical engine (`ci15m-exec-1`) with the recorder
tapes decompressed into `data/gemini_infoedge_15m/`:

```sh
python3 -I analysis/l5/btc15m_execution_audit/coverage_audit.py \
    data/gemini_infoedge_15m results/coverage.json
python3 -I analysis/l5/btc15m_execution_audit/maker_arms.py \
    data/gemini_infoedge_15m \
    --verify-fi-core analysis/gemini/favorite_inventory \
    --out results/maker_arms.json
# arm A, canonical engine, unchanged, after bridging settlement:
python3 -m analysis.gemini.info_edge.btc15m_shadow_policy
```

`settlement_bridge.py` is importable standalone and prints its own table.
