# L5 EMPIRICAL MODEL — canonical findings

**Status of this file.** New at commit `0bf8f0ee`+. It did not previously exist;
nothing here supersedes a prior version of this document because there was none.
It is the durable register of conclusions that later work must either build on or
explicitly overturn with materially better evidence.

Companion machine-readable copy: `analysis/l5/l5_empirical_model.json`.
Experiment ledger: `L5_EXPERIMENT_REGISTRY.md`.

Behavioural source of truth remains `L5_OPERATING_MODEL.md`. This file is
**economic**, not behavioural, and does not authorise any model, cap, threshold
or lane change.

## Label discipline

`EXPECTED` (modeled) · `ELIGIBLE` (program says we qualify) · `CREDITED`
(the venue actually paid it) · `REALIZED` (settled cash in our account). These
are never interchanged. Modeled rebate is never added into net.

---

## L5_ECON_001 — SUPPORTED
**L5's historical realized economics are incentive-dominated.**

$68,208 of $81,895 total profit = **83.3%** from CREDITED incentives
(maker rebates $33,929 + liquidity rewards $26,956 + taker rebates $7,323).

- Source: `docs/L5_FULL_HISTORY_LEDGER_20260831.md`
- Population: complete Polymarket history of `l5zn1bwom8etsk`, 2026-03-19 →
  2026-08-31. 742,964 TRADE, 15,243 REDEEM, 109 MAKER_REBATE, 72 TAKER_REBATE,
  19 REWARD rows across **16,501 up/down windows**, $7,423,850 turnover.
- Reconciliation: per-market against the venue's own closed-position `cashPnl`
  over 1,459 settled markets; median abs diff $0.49, 74.8% within $1.
- Precision caveat carried from source: reconstructed grand total (+$81.9k) does
  not exactly match the profile's ~$70.3k P/L + ~$3.0k open positions.
- Derived: `analysis/pmus/L5_ADVANTAGE_DECOMPOSITION.md`, commit `0bf8f0ee`.

## L5_ECON_002 — SUPPORTED
**Matched economics are strongly positive; directional residual removes almost
all of it.**

Gross matched **+$155,076**; directional residual & timing **−$141,390** =
**91.17%** of matched destroyed; net trading cash **+$13,686**.

Per window: matched +$9.3980, residual −$8.5686, net trading +$0.8294.
Per dollar of turnover: +2.0889¢ matched, −1.9045¢ residual, +0.1844¢ net.

- Same source and population as L5_ECON_001.

## L5_ECON_003 — SUPPORTED
**Trading-only expectancy is weak relative to incentives.**

Observed **+0.1844¢/$** of turnover, weekly-block bootstrap 95% CI
**[−0.08, +0.55]¢/$**, **P(≤0) = 8.4%** — statistically indistinguishable from
zero. Total take (trading + incentives) is 1.10¢/$, CI [0.83, 1.31].

Corollary recorded in source: weekly take per dollar is flat at ~0.9–1.7¢/$ from
April to August. The $70k curve is a **turnover** story, not an improving-edge
story; ramp outran cumulative profit ~100:1 and was funded by capital injection.

- Same source and population as L5_ECON_001.

## PMUS_ECON_001 — SUPPORTED PRELIMINARY
**Frozen queue-aware PMUS economics, q=25, n=5 windows.**

matched **+$4.2825/window** · residual **−$4.0500** · fees **−$0.8007** ·
net **−$0.5682/window**.

q=50: matched +$7.9507 · residual −$8.1000 · fees −$1.5153 · net −$1.6646.

- Policy: θ=0.96, variant B, `fill_model='queue_aware'`, preregistered grid
  unchanged (`analysis/pmus/CLIP_CAPACITY_PREREG.md` + ECONOMIC_COMPLETE addendum).
- Population: 5 usable BTC15m windows of 2026-09-27 — 1830z (UP), 1845z (UP),
  1900z (DOWN), 1915z (DOWN), 1930z (UP). 1815z excluded (collector started
  mid-window, 0 two-sided states). Tape
  `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl`, commit `19955307`.
- Completeness: each observed from first open through a terminal state with no
  bbo gap above 32s (HEARTBEAT_S 30 + one 2s poll).
- Verification: independent cashflow reconstruction
  (`scripts/audit_economic_complete.py`) reconciles **630 configurations**
  (5 windows × 3 fill models × 7 clips × 6 θ/variant) with 0 mismatches.
- **PRELIMINARY: n=5 against a preregistered checkpoint of 25.** Not an economic
  result. Do not size, tune or promote from it.

## PMUS_ECON_002 — SUPPORTED PRELIMINARY
**The largest measured PMUS-vs-L5 gaps are missing credited incentives and
trading fees, jointly 89.2% of the q=25 gap.**

Total gap L5 +$4.9630/window vs PMUS −$0.5682 = **$5.5312/window**:

| component | $/window | share |
|---|---|---|
| gross trading (matched + residual) | +0.5969 | 10.8% |
| fees (L5 pays $0) | +0.8007 | 14.5% |
| **credited incentives** | **+4.1336** | **74.7%** |

At q=50 the gap is $6.6276: trading 14.8%, fees 22.9%, incentives 62.4% = 85.3%
jointly.

- Scope caveat: "in this comparison" — L5 population is 16,501 windows over 5.5
  months on Polymarket; PMUS is 5 windows on one day on PMUS. The two are not
  matched samples and the comparison is a decomposition, not a controlled test.

## PMUS_ECON_003 — SUPPORTED
**Authoritative observed PMUS credited incentives remain $0.**

CREDITED rebate is $0, confirmed zero three independent ways, never credited.
Modeled maker rebate (θ_maker = 0.0125) is reported on its own line and is
**never** added into net. **Do not substitute modeled or raw rebates for
credited rebates.**

- Source: `analysis/pmus/CLIP_CAPACITY_PREREG.md` scoring rules; enforced in
  `src/pmus_completion_shadow.py` (`credited_rebate` hard-wired to D(0)).

## RESIDUAL_001 — SUPPORTED
**Residual losses are structural to L5 behaviour, not by themselves evidence of
clone failure.**

L5 loses 91.17% of matched economics to residuals; PMUS loses 94.57% at q=25 and
101.88% at q=50. Nearly identical. L5 never sells ($2.18 of sell proceeds across
742,964 trades), so residual is present in essentially every window at ~100%
frequency and is a deliberate accepted cost.

Independently, the canonical complement-response corpus
(`src/l5_complement_response_curve.py`, 71,354 inventory-changing fills) shows
L5 leans to the **complement** after the held side rises (+2..+10c → 63.5–67.7%
complement), so our post-completion complement acquisition is **L5-faithful**.
Inventory imbalance is the weakest predictor and OOS it *hurts* (−0.28%).

## RESIDUAL_002 — UNCERTAIN / IMPORTANT
**PMUS residual loss per unit is substantially worse than L5's historical
distribution. Mechanism not yet identified.**

PMUS observed **−$0.4050/unit**, 100% of it in the 25–45c acquisition band.
L5 weighted **−$0.0889/unit** across 224,688 residual units, with **36.2% in
positive-expectancy bands** (55–75c +0.0508, 75–90c +0.1382, >90c +0.0694).
Within the same 25–45c band L5 is −0.2487/unit, so PMUS is **1.63× worse in the
same band and 4.6× worse overall**.

Open: is the gap band composition, clip sizing, window length (15m vs his 5m
era), venue microstructure, or n=1 residual episode? **n=1 terminal residual
episode.** This is the highest-value open diagnostic and is NOT yet explained.

## THETA_001 — FROZEN
**θ=0.96 remains preregistered. The favourable θ=1.02 n=5 observation must not
cause selection or tuning.**

Recorded sweep (queue-aware, variant B, per window, n=5) — **observation only**:

| θ | q25 net | q50 net |
|---|---|---|
| **0.96 (frozen)** | **−0.5682** | **−1.6646** |
| 0.98 | +2.4305 | +4.3303 |
| 1.00 | +0.8916 | +2.0167 |
| 1.02 | **+4.3304** | **+7.6877** |
| 1.05 | +3.5620 | +6.6714 |

At n=2 the ordering was the reverse: θ=0.96 was the only positive setting. A
sign flip between n=2 and n=5 is the signature of noise, not of a better
threshold. Additionally, L5's own tolerance of >$1 pairs (30–45% of his matched
pairs, median cost 0.975) is a deliberate purchase of **incentives** with
guaranteed trading losses — a trade that is simply a loss while PMUS credited
incentives are $0 (PMUS_ECON_003). Loosening θ is therefore unsupported on two
independent grounds.

---

## Superseded conclusions

| superseded | by | why |
|---|---|---|
| ECONOMIC_COMPLETE beats/loses to B′ by the pre-audit numbers (EC net q25 +3.6830/window) | `1ea8760e` | The completion taker fee was subtracted twice in `net`. Sign of the B′-vs-EC delta reversed after the fix. |
| "EC carries zero residual, so it is not directionally exposed at settlement" | `3a92c583` | True only under the optimistic fill model. Under queue-aware fills 1830z ends 50 contracts net DOWN and loses the residual outright. EC does not guarantee a flat book. |
| Queue-ahead = whole-ladder `bid_shares`/`ask_shares` | `3a92c583` | Whole-ladder sum (~20,000) is not queue-ahead; the touch level holds ~100–1,600. Replaced by exact-price-level displayed quantity. |
| Pooled median completion edge ≈ 13.67¢ | `1ea8760e` | Was a median-of-medians. Pooled median over all 24 completion events is **8.57¢**. |
| "The completion edge is fee-safe pair economics" | `1ea8760e` | Paying n·(1−bid) and collecting n·$1 is identically n·(bid − basis): EC is **selling the aged leg into the bid**. Fresh two-sided pair cost is 1.01–1.03 at every completion instant, never below $1. The edge is directional carry. |
| ATOMIC_RECYCLE as a candidate state-consistency fix | `7c11bc23` | Falsified and no-op. No stale passive orders exist to cancel; residual is 100% from new post-completion decisions. |
| Queue-aware q25 −6.9442 / q50 −13.9588 (n=2) | `19955307` | n=2, both windows UP. At n=5 (3 UP / 2 DOWN): −0.5682 / −1.6646. |
| θ=0.96 is the only positive point of the sweep (n=2) | `19955307` | Reversed at n=5. Neither reading is acted on; θ stays frozen (THETA_001). |

## Commit lineage

`5edd2fab` runner import fix (authoritative pre-audit HEAD) →
`b62acf3e` tape scrape 1830z tail + 1845z →
`1ea8760e` **double-fee fix + conservation audit** →
`3a92c583` **level-accurate queue model, three fill bounds** →
`7c11bc23` completion state semantics, ATOMIC_RECYCLE falsified →
`19955307` 1900z/1915z/1930z scraped and scored, n=5 →
`0bf8f0ee` L5 advantage decomposition.

---

# Additions at commit `8bc089b4`+ (EXP-004 / EXP-006)

## PMUS_ECON_003 — AMENDED
Prior text ("credited incentives remain $0") was wrong for maker rebates.
Corrected: credited **liquidity rewards $0** and **taker rebates $0**; credited
**maker rebates +$2.1300** over 293 fills / 55 markets / 19 settled markets /
$554.4908 basis = **0.384% of notional**, from the realized bridge (venue numbers
only). Modeled rebate is still never substituted for credited.

## PMUS_ECON_002 — SCOPE NARROWED
"Missing incentives are 74.7% of the gap" holds **only for the queue-aware
BTC15m shadow**. `src/l5_full_wallet_economics.py` measures the live-probe lane
at −6.458%/dollar of trading loss against L5's −0.037%, and concludes there that
the binding constraint is *not* the payment. Both are true of their own
populations; the shadow loses 0.38%/dollar at q25, the probe lane 17× more.

## PMUS_INC_001 — SUPPORTED
**BTC15m is in no liquidity program.** All 54 `category=CRY` programs on
`/v1/incentives` are long-dated (`100k`, `150k`, `above-yr`, `hitprice`,
`pricerange`); zero matches for `updown` or `15m`. The crypto program
`crypto_20260924` pays a **shared $400/day pool**, `discountFactor 0.3`,
`targetSize 15000`; `maxSpread` is not published for crypto (NOT SOURCEABLE).
Achievable PMUS incentive is therefore maker rebate only: **0.396% of notional
= 43% of L5's 0.919%**. The missing 57% is program access, not rebate pricing.

## PMUS_INC_002 — SUPPORTED
Maker rebate = `0.0125 × C × p × (1−p)`, **banker's rounding to the cent per
fill**, credited at the point of trade. All 99 published price rows reproduce
exactly. **q=1 earns $0.00 at every price** (max unrounded $0.0031) — per-fill
rounding annihilates the rebate at clip 1. EXPECTED on observed queue-aware
fills: **q25 +$0.5020/window, q50 +$1.0020/window**, cross-validating against the
independently CREDITED 0.384%.

## RESIDUAL_002 — RESOLVED (supersedes the UNCERTAIN entry)
**The −$0.4050/unit vs −$0.0889/unit "gap" is not a behavioural deficiency.**

A residual pays $1 if it wins and $0 if it loses, so per unit `E = w − b`. This
reproduces L5's published band table exactly (weighted w = 0.3540, weighted
b = 0.4429, E = −0.0889 ✓; implied 25–45c w = 0.1013 and 75–90c w = 0.9632,
matching the module's stated "near 10%" and "near 96%").

Our 1830z residual: 50 DOWN at basis 0.4050, window settled UP, so it **lost —
and a losing residual loses exactly its basis.** −0.4050 is arithmetic.
Decomposition of the 0.3161 difference:

| term | $/unit |
|---|---|
| band/basis concentration (our 0.405 in his worst band) | −0.2148 |
| single-draw loss vs that band's 0.1013 win rate | −0.1013 |

L5's weighted residual basis (0.4429) is **higher** than ours. He is not buying
cheaper residual — **his residual wins 35.4% of the time against 10.1% in the
cheap band**, because 36.2% of his residual units sit at 55–90c where win rates
run 70–96%. That is the "adverse selection on what fails to pair" effect:
his unpaired leg is the side the market had already decided, i.e. the likely
winner.

## RESIDUAL_003 — SUPPORTED (mechanism, causal, observable)
**ECONOMIC_COMPLETE systematically retires the winning leg.** EC is arithmetically
"sell the aged leg into the bid" (superseded-claims table). The aged leg is the
one that *rose*, i.e. the high-basis likely winner. EC sells it and leaves the
cheap complement as residual. L5 never sells ($2.18 of sell proceeds in 742,964
trades) and therefore keeps it.

Measured on the already-preregistered arms, queue-aware, n=5:

| arm | residual episodes | residual qty | $/unit | weighted basis | wins |
|---|---|---|---|---|---|
| B′ baseline (θ=None) | 4 of 5 | 40/win | **+0.1702** | 0.5798 | **3/4** |
| B′+EC (θ=0.96) | 1 of 5 | 10/win | **−0.4050** | 0.4050 | **0/1** |

This is a real, observable behavioural difference from L5 that we had not
reproduced. It is recorded as mechanism, **not** as authority to change anything.

## BPRIME_001 — FALSIFIED AS A PATH TO $5
B′ baseline nets **+$5.70/window at q25 and +$11.40 at q50** under queue-aware
fills — above target — and it **must not be adopted**. Its matched economics are
**negative** (−1.1088 at q25); the entire net is unhedged directional residual
(+6.8088) resting on a **3-of-4 win rate over 4 episodes**.

Re-priced at L5's own measured residual win rate of 0.3540 with B′'s observed
basis 0.5798:

| q | at observed 3/4 | at L5 measured 0.3540 | swing |
|---|---|---|---|
| 25 | **+5.6992** | **−10.1408** | $15.84/window |
| 50 | **+11.3984** | **−20.2816** | $31.68/window |

A single win-rate assumption moves the answer by $15.84–$31.68 per window. B′'s
apparent success is a directional draw at n=4, and the standing rule that
residual exposure carries no predictive skill applies. **Do not test B′ as a
profit path again without a residual win-rate estimate on n≥25.**

## TARGET_001 — SUPPORTED PRELIMINARY
**No executable path to ≥$5/window exists on current evidence.**

Frozen B′+EC θ=0.96 queue-aware, per window:

| line | q=25 | q=50 |
|---|---|---|
| matched gross | +4.2825 | +7.9507 |
| taker fees | −0.8007 | −1.5153 |
| = matched net | +3.4818 | +6.4354 |
| terminal residual (observed) | −4.0500 | −8.1000 |
| credited incentives EXPECTED | +0.5020 | +1.0020 |
| liquidity rewards | 0.0000 | 0.0000 |
| **REALIZED-equivalent (observed residual)** | **−0.0662** | **−0.6626** |
| **PROJECTED (residual at band expectation)** | **+0.9468** | **+1.3634** |
| **distance to +$5** | **$4.0532** | **$3.6366** |

Reaching $5 on the projected path needs **5.3× scale at q25 / 3.7× at q50**
(~q=185), against a remaining research-loss budget of **$0.49320** and an
observed single-episode residual loss of $20.25 at q25 — 41× that budget.
Capital is not the constraint; the loss budget and the absent liquidity program are.

---

# Amendments from the adversarial audit (commit `a26269a8`+)

**NEW LEARNING: n=5 does not establish the terminal-residual frequency. The
earlier rejection was premature.** BTC15m remains the primary lane until
checkpoint 25 resolves this.

## TARGET_001 — AMENDED: NOT ESTABLISHED OR EXCLUDED
Prior text claimed "no executable path to ≥$5/window exists on current
evidence." **That was overstated.** The claim rested on treating −$8.10/window
residual (q50, n=5) as an expectation when 100% of it came from one window. The
Wilson 95% CI on a 1-of-5 frequency is [0.0362, 0.6245], which propagated through
q50 gives net from **+$5.97 to −$17.85** — the interval **spans the target**.

Corrected status: **$5/window is NEITHER DEMONSTRATED NOR EXCLUDED. Blocked on
terminal-residual frequency and severity estimation.** This is now the sole P0
for BTC15m.

At n=7 the instability is confirmed empirically: q25 moved from −0.5682 to
**+0.1067** and q50 from −1.6646 to **−0.2492** on two windows of new data.

## MATCHED_001 — NEW: allocation-convention uncertainty must be disclosed
Matched gross is **not a convention-free quantity.** The scorer allocates cost
between the matched and residual pools by **average cost**. Under FIFO the split
moves: at 1830z q50, average cost gives matched +18.5000 / residual −40.5000,
FIFO gives **+14.5000 / −36.5000** — **$4.00 of P&L moves between buckets** on
convention alone. Matched gross/window at q50 (n=5) is +7.9507 average-cost vs
**+7.1507 FIFO**, a **±$0.80/window** band.

Total net is invariant under the choice. Every matched-economics figure in this
programme is average-cost and carries that band. The *character* of matched
economics is still non-directional: a held matched pair pays exactly $1 whatever
settles.

## CAPACITY_001 — NEW: q above 5.31 contracts is modeled, not demonstrated
**Largest clip ever FILLED on an authenticated live PMUS order: 5.31 contracts**
(`analysis/l5/r4_orders.jsonl`, max `filled_qty`; max posted 5.60; capped by
`executed_safe_size` $2.4957). 10 of 64 posted orders filled; 41 hit deadline
unfilled, 12 were rejected zero-fill.

| clip | status |
|---|---|
| q ≤ 5 | within demonstrated live capacity |
| q=10 | 1.88× — **modeled** |
| q=25 | **4.71× — modeled** |
| q=50 | **9.42× — modeled** |

Separately, the queue-aware model **never truncates** below q=100 (0% of fills
capped by available volume) and fills/window is q-invariant at ~8.4, so the model
scales **exactly linearly** in q. Every q above 5.31 is therefore **modeled
clip-size extrapolation, not demonstrated executable capacity**, and a capacity
experiment is required before any q>5 figure is treated as achievable.

The earlier "historical mean fill was 3.98 contracts, so q=25 is 6.3× unachieved"
framing was wrong in its reasoning: 3.98 was the *mean* under our own $2.50
action cap. The correct bound is the *maximum ever filled*, 5.31, which is still
far below q=25.

## P0_RESIDUAL_001 — the sole P0
At n=7, queue-aware θ=0.96 variant B:

| | q=25 | q=50 |
|---|---|---|
| residual events | 3 of 7 | 3 of 7 |
| frequency | 0.4286 | 0.4286 |
| **Wilson 95% CI** | **[0.1582, 0.7495]** | **[0.1582, 0.7495]** |
| per-episode losses | −20.25, −15.40, −0.50 | −40.50, −30.80, −1.00 |
| mean episode severity | −12.0500 | −24.1000 |
| residual loss/window | −5.1643 | −10.3286 |
| net before residual | +5.7410 | +11.0122 |
| **break-even frequency for $0** | **0.4764** | **0.4569** |
| **target frequency for +$5** | **0.0615** | **0.2495** |

Observed 0.4286 sits just **inside** break-even at both clips, which is why both
are marginally positive at n=7 (realized-equivalent q25 **+0.5767**, q50
**+0.6837**).

**The decisive asymmetry:** q50's target frequency for $5 (0.2495) lies **inside**
the current Wilson interval, so $5/window at q50 is **not excluded**. q25's target
(0.0615) lies **below** the interval's lower bound (0.1582), so q25 reaching $5 is
**effectively excluded** — but q25 is also the smaller extrapolation. Severity
itself is uncertain: the three observed episodes span an order of magnitude.

Ledger: `analysis/pmus/pmus_window_ledger_q25.json` / `_q50.json`, regenerated
every firing by `scripts/pmus_window_ledger.py`.

---

# Amendments 2026-09-27 (n=9, breach preservation, budget correction)

## BUDGET_001 — CORRECTED, CANONICAL
There is **one** cumulative live-research loss state and it is authoritative:
**realized to date −$4.50680000 against a $5.00 halt, leaving $0.49320.**

PROBE-CAP-02 is **NOT** an independent fresh $5.00 envelope. My earlier ledger
entry said it was; that was wrong and would have implied ~10× the available money.
It does not reset, supersede, reinterpret or enlarge the existing state.
**The proposed Rung-A $1.50 live-loss budget is therefore NOT AUTHORIZED.** Any
live rung fits inside $0.49320 or waits for a new explicit owner decision.

## BREACH_001 — SUPPORTED, PERMANENT
**The 2026-09-16 live breach.** The R1 canary was authorized for **one market at
$0.05** and traded **11+ markets**, committing **$19.3295** to one of them. Nothing
was bypassed and no gate was defeated — the system did what it was built to do,
and what it was built to do was wrong in two places
(`analysis/l5/live_probe/EXECUTION_ARCHITECTURE.md`):

1. **The campaign could rewrite its own limits.** The dual key only *arms*. Once
   armed, the campaign loaded three owner rung approvals dated 2026-09-06
   (`R2C`, `R2C2`, `R3L`) and applied them: per-market **$0.05 → $7.50**, daily
   stop **−$1.00 → −$15.00**. Authorization to *start* was read as authorization
   to *continue*, on someone else's ten-day-old decision.
2. **The spend counter was an allow-list of stage names.**
   `counts_against_market_cap()` admitted `spring_open*`/`spring_extend*` and
   excluded `spring_complete_*`. A COMPLETE on this venue is a **BUY of the
   complement** — cash out. The counter read **$7.4476** against its $7.50 cap
   while real money-moving notional reached **$19.3295**. The `<=1.00` pair
   invariant bounds the *price per pair*, never the *dollar volume*: 20.44 pairs
   at 0.9457 honours it perfectly.

## RAMP_LADDER_001 — DO NOT USE FOR LIVE AUTHORIZATION
**`src/pmus_l5_ramp_ladder.py`'s owner-rung mechanism must never again be used to
authorize live sizing.** It is fault (1) above: persisted historical rung
approvals were auto-loaded by a running campaign and silently replaced live
limits. I proposed using exactly this path for EXP-010 Rung A before finding the
breach report; **that proposal is retracted.**

Live authorization is the **immutable mandate only**
(`analysis/l5/authorization/live_mandate.json`, `src/pmus_live_mandate.py`):
every field required with no defaults, `frozen=True`, expiring, re-read every
pass. The ramp ladder may still describe *shadow* rung sizing; it may not confer
live authority.

## MATCHED_002 — SUPPORTED: matched economics are NOT reliably positive
Superseding the earlier reading that matched economics were positive at every clip
and fill model — that held at n=5 and n=7 and was a sample artifact.

**θ=0.96 gates COMPLETIONS, not passive acquisitions.** A whipsawing market
produces passively-acquired pairs costing **more than $1.00**. Measured at 2030z,
q=50: UP bought at 0.42, 0.27, then 0.99; DOWN at 0.79 → average UP 0.5600,
average DOWN 0.7900, **pair cost 1.3500**, so each matched pair loses $0.35 —
matched gross **−$29.1087** for that window alone.

This is the same phenomenon the L5 ledger reports for him (30–45% of his matched
pairs cost more than $1.00, median 0.975). It is structural, not a defect.

Effect on the aggregate: q50 matched gross/window fell from **+11.6058 (n=7)** to
**+4.9591 (n=9)** on two windows.

## P0_RESIDUAL_001 — UPDATED to n=9
| | q=25 | q=50 |
|---|---|---|
| matched gross | +2.6944 | +4.9591 |
| taker fees | −0.7250 | −1.3920 |
| = matched net | +1.9694 | +3.5671 |
| expected maker rebate | +0.4089 | +0.8144 |
| **= before residual** | **+2.3783** | **+4.3816** |
| residual P&L | −4.0167 | −8.0333 |
| **= TOTAL/window** | **−1.6384** | **−3.6518** |
| residual events | 3 of 9 | 3 of 9 |
| frequency / Wilson 95% | 0.3333 / **[0.1206, 0.6458]** | same |
| mean episode severity | −12.0500 | −24.1000 |
| break-even residual frequency | 0.1974 | 0.1818 |
| **target frequency for +$5** | **IMPOSSIBLE** | **IMPOSSIBLE** |

**New at n=9: before-residual net is now BELOW $5 at both clips ($2.38 / $4.38),
so $5/window is unreachable even with perfect flattening — zero residual would
still not get there.** At n=7 before-residual was $11.01 and $5 was reachable at a
residual frequency ≤0.2495. This is a materially worse reading driven by
MATCHED_002, not by the residual term.

Observed residual frequency 0.3333 now sits **above** break-even at both clips.
Still n=9 of 25; the Wilson interval remains wide and no rejection is recorded
from it (TARGET_001 rule).

## MATCHED_003 — the two matching mechanisms are near-equal and opposite
`scripts/pmus_matched_decomposition.py`, n=10 usable BTC15m windows, frozen policy
(θ=0.96, variant B, queue_aware). **Diagnostic only; gates nothing.**

Every matched unit is attributed to the mechanism that created it:

* **EC** — retired by an ECONOMIC_COMPLETE taker completion (θ-gated).
* **PASSIVE** — matched by a later passive ACQUIRE/EXTEND on the opposite side,
  i.e. the oscillator. **Not gated by θ or by anything else.**

Authoritative split, taken from the scorer itself (`gross_margin` is the EC leg,
`matched_pnl − gross_margin` the passive leg), so it reconciles exactly:

| per window | q=25 | q=50 |
|---|---|---|
| EC-created matched gross (n=5) | +11.8600 | +22.7999 |
| PASSIVE-created matched gross (n=5) | −7.5775 | −14.8491 |
| **= total matched gross (n=5)** | **+4.2825** | **+7.9507** |
| EC-created matched gross (n=10) | +9.6741 | +18.8066 |
| PASSIVE-created matched gross (n=10) | −5.6472 | −11.1414 |
| **= total matched gross (n=10)** | **+4.0269** | **+7.6652** |

n=15/20/25: NOT YET AVAILABLE.

**The oscillator destroys 58–59% of what completion earns.** Completion is the
only mechanism with positive measured economics; passive matching is negative in
7 of 10 windows at both clips. θ binds one path and nothing binds the other.

### Convention disclosure (MATCHED_001 band)
The EC leg is priced at the scorer's own running-average basis and therefore ties
the scorer **to 1e-9 in all 10 windows** (asserted in the script). The passive leg
does **not** tie: the per-unit path prices surviving lots **FIFO**, the scorer
prices them at **running average**. Totals over matched+residual are invariant;
the split is not. Per-window `passive_convention_delta` ranges **−25.3611 to
+8.5198 (q=50)** — larger than the matched figure itself in 1930z. Both files
carry `passive_matched_gross_avg_cost` (authoritative) and
`passive_matched_gross_fifo_path` (path pricing) side by side. **The FIFO path is
kept only because it is the only convention under which an individual >$1 pair is
visible at all**; the average-cost convention smears it across the window.

## PAIRCOST_001 — a third of matched units pair above $1.00
Quantity share of matched units by effective pair cost, FIFO path pricing:

| bucket | q=25 n=5 | q=25 n=10 | q=50 n=5 | q=50 n=10 |
|---|---|---|---|---|
| <0.96 | 0.6311 | 0.6370 | 0.6260 | 0.6333 |
| 0.96–1.00 | 0.0000 | 0.0182 | 0.0000 | 0.0183 |
| 1.00–1.10 | 0.1341 | 0.1452 | 0.1360 | 0.1467 |
| 1.10–1.25 | 0.0335 | 0.0726 | 0.0340 | 0.0733 |
| >1.25 | 0.2012 | 0.1271 | 0.2040 | 0.1283 |

**Above $1.00: 34.5% of units at n=10 (both clips). Nothing lands in 0.96–1.00
except one 50-lot** — the distribution is bimodal, θ-compliant below 0.96 or
outright loss-making above 1.00, with almost nothing in between. That is the
signature of a gate on one path and no gate on the other, not of a tunable
threshold.

## WHIPSAW_001 — the expensive pair is GENUINE L5 behavior plus an ACCOUNTING artifact
Action-by-action reconstruction of 2030z at q=50 (`scripts/audit_completion_orderstate.py`),
against the canonical corpus in `src/l5_complement_response_curve.py`. **No new
rule, no θ selection.**

UP probability on tape: 0.42 (20:36) → 0.17 (20:41:53) → **0.19 (20:42:24)** →
0.26 → 0.35 → **0.92 (20:43:55)** → 0.99 → settled **UP**.

| t | action | inv before | inv after | px | pair cost | TTE |
|---|---|---|---|---|---|---|
| 20:36:46 | ACQUIRE UP 50 | 0/0 | 50/0 | 0.4200 | — | 494s |
| 20:39:49 | COMPLETE DOWN 50 | 50/0 | 0/0 | 0.5200 | **0.9400** | 311s |
| 20:40:19 | ACQUIRE UP 50 | 0/0 | 50/0 | 0.2700 | — | 281s |
| 20:40:21 | COMPLETE DOWN 50 | 50/0 | 0/0 | 0.592174 | **0.862174** | 279s |
| 20:42:24 | ACQUIRE DOWN 50 | 0/0 | 0/50 | 0.7900 | — | 156s |
| 20:44:56 | ACQUIRE UP 50 | 0/50 | 50/50 | 0.9900 | **1.7800** | **4s** |

Both completions were clean (0.9400, 0.8622). The loss is the last two rows.

**The 20:42:24 DOWN open is genuine L5 behavior.** Flat inventory, previous action
a COMPLETE, and the probability had fallen 0.42→0.17 over the preceding five
minutes — movement favoured DOWN. The corpus says
`4_movement_decides_next_side: STRONGLY SUPPORTED` with
`MOVEMENT_DOMINATED_SHARE = 0.780`; `LEG_PATTERN` carries an explicit first-leg
band at **0.800** completing at 0.120 for a 0.9500 pair; and
`RESIDUAL_PNL_PER_UNIT` makes **75–90c L5's single best residual band at
+0.1382/unit**. Our clone opened where L5 opens, on the signal L5 uses, in L5's
most profitable band. It then lost, once.

**The 20:44:56 UP buy at 0.99 is outside L5's observed repertoire but is NOT the
loss.** All five `LEG_PATTERN` completion bands land at pair cost 0.91–0.95,
`PROFITABLE_MEDIAN_COST = 0.9300`; no band above 1.00 appears anywhere in the
corpus. On the response curve this state is the extreme `<=-10c` bucket (held side
collapsed 0.81→0.01): L5 goes **same side 0.523 / complement 0.477** — a coin
flip that slightly prefers averaging down, never a reliable completion. And TTE=4s
has no counterpart in the corpus at all, so this is **NOT COMPARABLE rather than
contradicted**.

Economically the late buy **helped**: carrying 50 DOWN @0.79 to settlement costs
**−$39.5000**; paying 0.99 for the complement cost **−$39.0000** and earned a
maker rebate instead of a taker fee. **+$0.50 better.**

### Verdict
> The **first leg** is genuine L5 behavior. The **$1.78 pair cost** is a
> translation/accounting artifact: our accounting converts an already-doomed
> directional residual into a >$1 matched pair four seconds before expiry. L5's
> tape never shows such a pair because L5 lets that leg settle as residual — the
> same dollars, filed under a different heading.

Two consequences, both recorded rather than acted on:

1. **The >$1.10 pair-cost buckets in PAIRCOST_001 are contaminated with
   directional residual loss.** The matched/residual split is clean in total and
   not clean bucket by bucket.
2. **The genuine divergence from L5 in this window is the clock, not the rule.** A
   15-minute PMUS window forced this first leg to resolve inside 156s. L5's median
   leg gap is **47s** with **16.7% of pairs beyond 120s** and no expiry at that
   scale; `PROFIT_BY_GAP` shows L5's pair profit *rising* with the wait (+0.02 at
   1–5s to +0.11 beyond 120s). Patience is the thing PMUS BTC15m cannot give us.

The MATCHED_002 figure of **1.3500** and this **1.7800** are both correct and are
not in conflict: 1.3500 is the window average-cost pair (avg UP 0.5600 over
0.42/0.27/0.99, plus DOWN 0.7900); 1.7800 is the worst individual FIFO pair. The
average-cost convention is what the scorer's P&L uses; the FIFO path is what makes
the single bad pair visible.

## EXPOSURE_001 — terminal residual frequency is NOT directional-exposure frequency
Peak vs terminal one-sided inventory, q=50, n=10:

| | windows |
|---|---|
| peak one-sided exposure ≥ 1 clip | **10 of 10** |
| peak ≥ 2 clips | 8 of 10 |
| **terminal** residual at expiry | **3 of 10** |

The preregistered P0 statistic counts only the terminal state. 2030z carried a
50-lot one-sided position for 152s, lost $39.00 on it, and is recorded as
**zero residual** because it was matched at the buzzer. **Directional risk was
taken in every window; it failed to flatten in three.** The P0 metric measures the
flattening failure rate, not the exposure rate, and a share of the directional
loss is booked inside matched gross.

**This does not change the preregistered decision rule and no threshold was
touched.** It is recorded so the n=25 decision is not read as "residual risk occurs
30% of the time".

## P0_RESIDUAL_001 — UPDATED to n=10
| | q=25 | q=50 |
|---|---|---|
| matched gross | +4.0269 | +7.6652 |
| taker fees | −0.9381 | −1.8241 |
| expected maker rebate | +0.4380 | +0.8710 |
| **= before residual** | **+3.5268** | **+6.7121** |
| residual P&L | −3.6150 | −7.2300 |
| **= TOTAL/window** | **−0.5262** | **−1.3889** |
| residual events | 3 of 10 | 3 of 10 |
| frequency / Wilson 95% | 0.3000 / **[0.1078, 0.6032]** | same |
| mean episode severity | −12.0500 | −24.1000 |
| break-even residual frequency | **0.2927** | **0.2785** |
| target frequency for +$5 | **IMPOSSIBLE** | 0.0710 |

Windows: 1830z UP, 1845z UP, 1900z DOWN, 1915z DOWN, 1930z UP, 1945z DOWN,
2000z UP, 2030z UP, 2045z DOWN, 2115z DOWN (5 UP / 5 DOWN). Excluded: 1815z
(collector started mid-window), 2015z and 2100z (no terminal row on tape).

Both clips improved against n=9 (q50 total −3.6518 → −1.3889) on one good window
(2115z, +26.31). Observed frequency 0.3000 still sits **above** break-even at both
clips. At q=50, +$5/window is now arithmetically reachable again — it needs a
residual frequency of **0.0710**, which is **outside the lower Wilson bound of
0.1078**. Still n=10 of 25; no rejection is recorded from the interval
(TARGET_001 rule).

## ACCOUNTING_001 — the checkpoint's eight rows, and the line to read first
`total_net` and `realized_equivalent` are **retired** (EXP-013). The second was
the first plus a MODELED maker rebate while being named "realized", and no
published line was free of an incentive term. Replaced by:

| per window | q=25 n=10 | q=50 n=10 |
|---|---|---|
| EC matched gross | +9.6741 | +18.8066 |
| PASSIVE matched gross | −5.6472 | −11.1414 |
| **TOTAL matched gross** | **+4.0269** | **+7.6652** |
| fees | 0.9381 | 1.8241 |
| residual P&L | −3.6150 | −7.2300 |
| **SHADOW TRADING NET** | **−0.5262** | **−1.3889** |
| expected maker rebate | +0.4380 | +0.8710 |
| **SHADOW MODELED NET incl. EXPECTED rebate** | **−0.0882** | **−0.5179** |

n=5 for comparison: shadow trading net −0.5682 / −1.6646, shadow modeled net
−0.0662 / −0.6626.

`fees` is a **positive cost magnitude** and is subtracted; `residual_pnl` carries
its own sign and is added, so:

    ec_matched_gross + passive_matched_gross    = matched_gross
    matched_gross - fees + residual_pnl         = shadow_trading_net
    shadow_trading_net + expected_maker_rebate  = shadow_modeled_net_...

**Read `SHADOW TRADING NET` first: it is the only line with no incentive term in
it.** Both clips are negative on it at n=5 and n=10. The rebate is **EXPECTED** —
modeled from the published maker schedule against modeled maker fills, never
ELIGIBLE, CREDITED or REALIZED; credited PMUS liquidity rewards are $0 and BTC15m
is in no liquidity program. Even including it, both clips stay negative.

Enforced per window and at every rolling checkpoint by
`tests/test_pmus_shadow_net_identity.py`, which recomputes the trading line from
the scorer rather than from the file, rejects a negative fee, and fails any row
named "realized" or "credited". Reporting only: scorer, θ, fill model, q grid,
decision rule and the n=25 preregistration are unchanged and no number moved.

## COLLECTOR_001 — the missing terminal rows are a RACE, and it was destroying 31% of windows
Not bad luck and not venue behaviour. `series_slugs()` in `src/pmus_book_tape.py`
excludes events the venue marks `closed`, so a window **leaves the polling
universe at the first reslate after its boundary** — 0 to `SERIES_RESLATE_S`=45s
later, at random — while the venue publishes the terminal bbo state **6s to 30s+
after** that boundary. Whichever lands first decides whether the close is ever
recorded, and the preregistered completeness rule excludes any window whose last
tape state is still OPEN. A lost race therefore **silently destroys a window of
evidence**.

Measured 2026-09-27 across 16 observed windows:

| window | last bbo | terminal row | reslate that dropped it |
|---|---|---|---|
| 2115z | 21:30:30 | **21:30:30 EXPIRED** | 21:30:35 — **5s to spare** |
| 2130z | 21:44:52 | **none** | 21:45:19 |
| 2145z | 21:59:44 | **none** | 22:00:21 |
| 2200z | 22:14:33 | **none** | 22:15:14 |

**5 of 16 windows lost (31%): 2015z, 2100z, 2130z, 2145z, 2200z — the last three
consecutively.** Every captured window's last bbo row falls *after* its close;
every lost window's falls 5–8s *before* it. At a 31% loss rate, reaching n=25
needs ~36 windows, and the run of three shows the rate is not stationary.

**Fix (`DRAIN_S`=240s): a slug that leaves the universe starts a drain instead of
ending coverage** — it keeps being polled until its terminal state is recorded or
the bounded deadline passes, and a `drain_end` row states which happened, so a
genuinely unobservable close is visible on the tape rather than inferred from an
absence. `tests/test_pmus_tape_terminal_drain.py` (8 tests) drives the loop on a
fake clock with no network, and **its negative control reproduces the measured
loss at `DRAIN_S`=0** — so the test is known to have teeth. Also pinned: the drain
is bounded, the live window is not starved, and a slug is never polled twice in a
cycle.

**NOT DEPLOYED.** The fix is committed and tested; the running collector still
carries the race. Deploying it is a redeploy of `pmus-btc15m-tape`, which is an
owner decision — and until it ships, roughly a third of windows will keep being
lost. **This, not the arithmetic, is the binding constraint on reaching n=25.**
No window already in `USABLE` or `EXCLUDED` changes: the fix cannot recover a
close that was never observed.

## RESIDUAL_004 — the first WINNING residual, and a label it broke (n=11)
2215z carried a terminal residual that **won**: 100 UP at basis **0.9775** into an
**UP** settlement, so it paid $1/unit for **+2.25** at q=50. Three of four residual
episodes have now lost and one has won.

This falsifies nothing about the policy and changes no decision, but it broke two
field names that had quietly assumed a residual is always a loss.
`per_episode_losses` → **`per_episode_pnl`**, `mean_episode_loss` →
**`mean_episode_pnl`**, `loss_per_window` → **`episode_pnl_per_window`**, plus
explicit `episodes_that_lost` / `episodes_that_won` counts. **The arithmetic is
byte-identical and was already correctly signed** — only the labels were wrong.

The consequence matters for reading the P0 line: a winning episode **raises** the
signed mean and therefore **raises break-even frequency**, so break-even moved
from 0.2785 (n=10) to **0.4278** (n=11, q=50) partly because one episode won
rather than because the policy improved. `mean_episode_pnl` must never be read as
a severity.

### P0_RESIDUAL_001 — UPDATED to n=11 (2215z UP added)
| | q=25 | q=50 |
|---|---|---|
| EC matched gross | — | — |
| **= before residual** | **+3.9138** | **+7.4919** |
| residual episodes | 4 of 11 | 4 of 11 |
| frequency / Wilson 95% | 0.3636 / **[0.1517, 0.6462]** | same |
| signed mean episode P&L | −17.5125 | −17.5125 |
| episode P&L per window | — | −6.3682 |
| break-even residual frequency | 0.4470 | **0.4278** |
| frequency needed for +$5 | **IMPOSSIBLE** | 0.1423 |

Observed 0.3636 now sits **below** break-even at both clips — the first time that
has been true — and the +$5 target frequency of 0.1423 at q=50 is **inside** the
Wilson interval [0.1517, 0.6462]… but only just, and in fact 0.1423 < 0.1517, so
it remains **outside** the lower bound. No rejection and no establishment is
recorded either way (TARGET_001). n=11 of 25; both clips remain modeled
extrapolation at 4.71× and 9.42× the 5.31-contract largest live fill.

## COLLECTOR_002 — the drain is DEPLOYED but NOT YET EXERCISED
Deployment `fb6de07c`, commit `c70f662b`, `SUCCESS` 22:46:34Z on the existing
`pmus-btc15m-tape` service. Verified against the requested checklist:

| check | result |
|---|---|
| serving SHA contains the fix | **YES** — `c70f662b`; the process header row carries the new build and `provenance: public PMUS gateway, read-only, no credentials` |
| active window keeps recording | **YES** — booted 22:46:30 onto the live 2245z and recorded it through its close |
| a departing slug stays in drain polling | **NOT EXERCISED** — see below |
| polling ends on observed terminal state | **YES, by the stronger path** — 2245z was already in `terminal_seen`, so it never entered the drain at all |
| explicit `drain_end` after 240s | **NOT OBSERVED** — no drain has started, so none is due |
| no duplicate or corrupt rows | **YES** — 1885 rows, **0** duplicate `(slug,kind,t)` triples, **0** malformed bbo rows |
| scorer / θ / q grid / fill model / decisions / membership | **unchanged** |
| trading service redeployed or restarted | **NONE** — every other service's latest deployment timestamp is unchanged |

**First post-deploy close, stated precisely rather than favourably.** 2245z's
terminal row was captured at **23:00:04** `MARKET_STATE_CLOSED`; the reslate that
dropped the slug came at **23:00:35**, 31 seconds later. So **the close was
captured, but by the pre-existing path — the race was won on timing, and the drain
was never entered.** This close would have been captured by the old code too. The
fix is deployed and inert so far; the real test is the first close where the
reslate lands *before* the venue publishes, which is the 31%-of-windows case.
**No claim is made that the drain has been shown to work in production.**

Not a deployment artifact, checked because it looked like one: 2245z's first bbo
row is 210s after its open, but that is normal for this collector — observation
lateness across the 12 usable windows spans **21s to 257s** (1915z, already
admitted, is the worst). The redeploy did not truncate this window abnormally.

### P0_RESIDUAL_001 — UPDATED to n=12 (2245z UP added)
| | q=25 | q=50 |
|---|---|---|
| **= before residual** | **+3.1831** | **+6.0507** |
| residual episodes | 4 of 12 | 4 of 12 |
| frequency / Wilson 95% | 0.3333 / **[0.1381, 0.6094]** | same |
| signed mean episode P&L | −17.5125 | −17.5125 |
| break-even residual frequency | 0.3635 | **0.3455** |
| frequency needed for +$5 | **IMPOSSIBLE** | 0.0600 |

2245z carried **no terminal residual** and still lost: shadow trading net
**−10.65** at q=50, on matched pair cost alone. Observed frequency 0.3333 sits
just below break-even at both clips. The +$5 target of 0.0600 at q=50 is **well
outside** the Wilson lower bound of 0.1381. No rejection recorded (TARGET_001);
n=12 of 25. Both clips remain 4.71× and 9.42× modeled extrapolation.

## COLLECTOR_003 — the bounded drain FIRED IN PRODUCTION and saved a window
The verification I said was outstanding has now happened, on 2315z:

| event | time |
|---|---|
| window closed | 23:30:00 |
| last OPEN row observed | 23:29:44 |
| **reslate dropped the slug** | **23:30:54** |
| **venue published the close** | **23:32:58 MARKET_STATE_EXPIRED** |
| `drain_end` row, `terminal_recorded: true` | after that row |

**The close arrived 2m58s after the boundary and 2m04s after the slug had already
left the universe. Under the old code this window was gone.** That is the 31%
failure mode caught in the act, and it is the first genuinely new coverage the fix
has produced — 2245z and 2300z were both captured before their reslate, so the old
code would have got those anyway.

### …and the fix creates a rule conflict that is an OWNER DECISION
2315z is **EXCLUDED**, because the drain's own post-close polling produces a
**194.7s gap** between the last OPEN row and the terminal row, and the
preregistered rule says no bbo gap above 32s. Every other gap in that window is
**≤29.1s**, so the *trading* period is densely observed and the single long gap
lies **entirely after the close**. Settlement was UP (1.0000 vs lastTradePx
0.9900), so the window is otherwise sound.

Admitting it means reinterpreting a preregistered completeness rule in the
direction of including a window, which is not mine to do. **The drain will produce
a post-close gap on every future window, so this recurs until the rule is either
amended to apply only before the close, or reaffirmed as written.** Recorded rather
than resolved.

## P0_RESIDUAL_001 — UPDATED to n=13 (2300z UP added; 2315z excluded)
| | q=25 | q=50 |
|---|---|---|
| **= before residual** | **+5.0207** | **+9.7486** |
| residual episodes | 4 of 13 | 4 of 13 |
| frequency / Wilson 95% | 0.3077 / **[0.1268, 0.5763]** | same |
| signed mean episode P&L | −17.5125 | −17.5125 |
| break-even residual frequency | 0.5734 | **0.5567** |
| frequency needed for +$5 | 0.0024 | **0.2712** |

**Read this with the outlier in view.** 2300z alone produced a shadow trading net
of **+53.48** at q=50 with zero terminal residual, and it moved before-residual
from +6.05 to +9.75 in one step. Observed frequency 0.3077 now sits **above** the
+$5 target frequency of 0.2712 and **below** break-even 0.5567 for the first time —
but that ordering is one window old. No establishment is recorded, exactly as no
rejection was (TARGET_001). n=13 of 25.

## PROFIT_LOCK_5 at n=13 — crosses $5, and ENTIRELY because of one window
| q=50 | value |
|---|---|
| lock hit rate | 9 of 13 |
| SHADOW TRADING NET / window | **+7.1100** |
| …**median** | **+5.8911** |
| …**mean excluding 2300z** | **+3.2010** (exactly the n=12 figure) |
| $/locked window | +12.7875 |
| worst window | −10.6526 |
| max peak directional | **100, unchanged** |
| SHADOW MODELED NET | +7.9177 |

2300z locked at a guaranteed floor of **+54.0176** with 74.9s to expiry — slightly
*above* the frozen arm's +53.48, because the floor-securing completion added value.
**One observation moves the headline from +3.2010 to +7.1100.** The ≥$5 criterion is
arithmetically met at n=13 and is **NOT recorded as met**: a result that depends on
a single window is not a result. q=25 gives +3.1010, still short.

Unchanged: q=25 and q=50 remain 4.71× and 9.42× modeled extrapolation on the
5.31-contract largest live fill; the rebate is EXPECTED, never credited; and
CHANNEL_A remains unresolved (`analysis/l5/p1/P1_CHANNEL_A.md`). A-vs-B split at
n=13: zero A-only windows at q=50, one at q=25 (1945z).

## COMPLETENESS_RULE_AMENDMENT_001 — applied, and it changed exactly one window
Owner-authorized prospective clarification, recorded **2026-09-27T23:57:12Z in
commit `fea39051` BEFORE any window was scored under it**, with the previous rule,
the rationale and the expected effect on every excluded window all written down in
advance (`analysis/pmus/COMPLETENESS_RULE_AMENDMENT_001.md`).

`scripts/pmus_completeness_rule.py` is now the only place the rule lives and it
applies all six elements **mechanically to every window**; nothing is admitted by
hand. Result, against the prediction made before running:

| window | e1 | e2 | e3 | e4 | e6 | 2-sided | max PRE gap | post-close gap | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1815z | Y | Y | Y | Y | Y | **n** | 2.9s | 16.8s | fail (two-sided, unchanged) |
| **2315z** | Y | Y | Y | Y | Y | Y | **29.1s** | **194.7s** | **PASS → ADMITTED** |
| 2015z, 2100z, 2130z, 2145z, 2200z, 2230z | Y | Y | Y | **n** | Y | Y | ≤30.4s | **none** | fail (no terminal row, unchanged) |
| the 13 already-usable windows | Y | Y | Y | Y | Y | Y | ≤31.5s | 26.6–30.8s | PASS |

**The prediction held exactly: element 4 keeps all six no-terminal-row windows
excluded, 1815z still fails on two-sided states, and 2315z is the only change.** No
terminal observation was reconstructed from later venue data anywhere. Note the
already-usable windows all carry post-close gaps of 26.6–30.8s, so the amendment
regularises what was already being tolerated at the margin rather than carving out
a special case.

## P0_RESIDUAL_001 — UPDATED to n=14 (2315z UP admitted)
| | q=25 | q=50 |
|---|---|---|
| **= before residual** | — | **+10.1409** |
| residual episodes | 5 of 14 | 5 of 14 |
| frequency / Wilson 95% | 0.3571 / **[0.1634, 0.6124]** | same |
| signed mean episode P&L | −16.9300 | −16.9300 |
| break-even residual frequency | — | **0.5990** |
| frequency needed for +$5 | — | **0.3037** |

2315z contributed a shadow trading net of **exactly 0.0000** with a terminal
residual, so it moves the episode count without moving the P&L. 392 configs,
0 failures.

## TARGET_001 — TARGET CROSSED — NOT ESTABLISHED
Owner-directed label, and the n=14 numbers make it plainer than n=13 did.
PROFIT_LOCK_5, q=50, SHADOW TRADING NET before rebates:

| statistic | value |
|---|---|
| mean | **+7.0609** |
| median | +6.1573 |
| **trimmed mean (drop 1 each end)** | **+4.6240** |
| lock-hit rate | 10 of 14 |
| positive-window rate | 10 of 14 |
| worst window | 2245z, **−10.6526** |
| best window | 2300z, **+54.0176** |
| **best window's share of total P&L** | **0.5464** |
| **mean excluding best window** | **+3.4489** |
| max peak directional exposure | **100, unchanged from the frozen arm** |

**One window supplies 54.6% of all P&L, and removing it drops the mean from
+7.0609 to +3.4489. The trimmed mean, +4.6240, is below $5 on its own.** The target
is crossed on the mean and is **NOT ESTABLISHED**. q=25 is weaker still: mean
+2.8795, trimmed +2.3882, mean excluding best +1.0234, best window 67.0% of total.

From now on every mean is published with median, trimmed mean, lock-hit rate,
positive-window rate, worst, best, the best window's share of total P&L and the mean
excluding the best window — computed by `src/pmus_distribution.py`, pinned by
`tests/test_pmus_distribution.py` (7 tests, one of which is the n=13 case that
motivated the module). **No new cutoff for "not dependent on one observation" has
been invented: the complete distribution is reported and the reader judges.**

Unchanged: the $5 lock threshold is frozen, θ untouched, q grid untouched, no
rebate in the criterion, CHANNEL_A unresolved and `floor_guard` untuned, and both
clips remain 4.71× and 9.42× modeled extrapolation on the 5.31-contract largest
live fill. n=14 of 25.

## n=16 — the frozen arm's mean crosses $5 while its MEDIAN is NEGATIVE
2330z (DOWN, +35.0488) and 2345z (UP, −1.0696) admitted mechanically under
COMPLETENESS_RULE_AMENDMENT_001; both PASS all six elements with post-close gaps of
26.9s. 448 configs, 0 failures. **n=16 of 25, 9 remaining.** Rolling **n=15 is now
available** for the first time.

### Rolling n=15, frozen arm (P0)
| | q=25 | q=50 |
|---|---|---|
| EC matched gross | +11.2494 | +22.1133 |
| PASSIVE matched gross | −4.7119 | −9.3219 |
| TOTAL matched gross | +6.5375 | +12.7914 |
| fees | 0.9173 | 1.7993 |
| residual P&L | −2.7883 | −5.5767 |
| **SHADOW TRADING NET** | **+2.8319** | **+5.4154** |
| expected maker rebate | +0.4060 | +0.8047 |
| SHADOW MODELED NET | +3.2379 | +6.2201 |

**Distribution behind that q=50 mean of +5.4154 — this is why the diagnostics were
built:**

| statistic | value |
|---|---|
| mean | **+5.4154** |
| **median** | **−0.0688** |
| trimmed mean (drop 1 each end) | +4.5048 |
| **positive-window rate** | **6 of 15** |
| worst window | 2030z, −30.8152 |
| best window | 2300z, +53.4832 |
| best window's share of total P&L | **0.6584** |
| mean excluding best window | **+1.9820** |

**The frozen arm's mean clears $5 while its median is below zero and only 40% of
windows are profitable.** A mean and a median on opposite sides of zero is the
sharpest illustration yet that the mean alone cannot carry this decision. Nothing
is selected or changed from it.

### PROFIT_LOCK_5 at n=16 (threshold frozen at $5)
| | q=25 | q=50 |
|---|---|---|
| EC matched gross | +7.5455 | +13.6008 |
| PASSIVE matched gross | −3.5815 | −6.1039 |
| TOTAL matched gross | +3.9639 | +7.4969 |
| fees | 0.6682 | 1.1376 |
| residual P&L | −0.3997 | +0.1131 |
| **SHADOW TRADING NET** | **+2.8960** | **+6.4724** |
| median | +5.4226 | +5.8330 |
| trimmed mean | +2.4772 | +4.2995 |
| lock-hit rate | 9 of 16 | 11 of 16 |
| positive-window rate | 9 of 16 | 11 of 16 |
| worst window | 2030z, −15.3540 | 2245z, −10.6526 |
| best window | 2300z, +27.0088 | 2300z, +54.0176 |
| best window's share of total | 0.5829 | **0.5216** |
| mean excluding best window | +1.2885 | **+3.3027** |
| max peak directional | **50, unchanged** | **100, unchanged** |

Still **TARGET CROSSED — NOT ESTABLISHED**: q=50 mean +6.4724 with one window at
52.2% of total P&L and the mean dropping to +3.3027 without it. The trimmed mean,
+4.2995, remains below $5.

Residual episodes are now **7 of 16** (frequency 0.4375, Wilson [0.2310, 0.6682])
with **3 winners among them**, which lifts the signed mean episode P&L to −11.6915
and therefore lifts break-even frequency to 0.9326. That movement is a property of
the mixed signs in the episode population, not an improvement in the policy.

**Protocol frozen through n=25 per `analysis/pmus/CHECKPOINT25_FREEZE.md`. No
challenger was started, no parameter moved, and nothing was selected from these
numbers.**

## COLLECTOR_004 — a drain that EXPIRED, and the tape says so
0015z: the reslate at 00:30:30 started the bounded drain, it polled for the full
`DRAIN_S`=240s, and it emitted **`drain_end` at 00:34:31 with
`terminal_recorded: FALSE`**. The venue had still not published a terminal state.

**This is the opposite of the old failure.** Before the fix, a lost close was a
silent absence; now the tape records that the collector waited and gave up, so the
exclusion rests on evidence instead of an inference. 0015z is **excluded
permanently**. The venue later reported `settlementPx` 1.0000 vs `lastTradePx`
0.9900 (UP) and that is deliberately **not** used — reconstructing a missing
terminal observation from later venue data is forbidden.

Data point on `DRAIN_S` sufficiency, recorded and **not acted on**: 2315z needed
**124s** after its reslate and was caught; 0015z needed **more than 241s** and was
not. 240s is therefore not always enough. `DRAIN_S` is a frozen parameter and
changing it would require a deployment that is not authorized, so this is a
finding, not a change.

## n=17 — 0000z admitted (+33.4325); 8 windows remain
476 configs, 0 failures. 0000z is a **third** large winner (DOWN, shadow trading
net **+33.4325**, zero terminal residual), after 2300z (+53.48) and 2330z (+35.05).

### Rolling n=15, frozen arm (P0) — mean above $5, median still below zero
| | q=25 | q=50 |
|---|---|---|
| EC matched gross | +11.5727 | +23.0398 |
| PASSIVE matched gross | −5.1124 | −10.3946 |
| TOTAL matched gross | +6.4603 | +12.6452 |
| fees | 0.8887 | 1.6942 |
| residual P&L | −2.8217 | −5.6433 |
| **SHADOW TRADING NET** | **+2.7499** | **+5.3076** |
| expected maker rebate | +0.4093 | +0.8127 |
| SHADOW MODELED NET | +3.1593 | +6.1203 |

q=50 distribution: median **−0.0688**, trimmed mean +4.3805, **6 of 15 positive**,
best window **67.2%** of total P&L, mean excluding best **+1.8665**.

### PROFIT_LOCK_5 at n=17 (threshold frozen at $5)
| | q=25 | q=50 |
|---|---|---|
| EC matched gross | +7.5281 | +13.6537 |
| PASSIVE matched gross | −3.3708 | −5.7448 |
| TOTAL matched gross | +4.1572 | +7.9089 |
| fees | 0.6544 | 1.1216 |
| residual P&L | −0.3762 | +0.1064 |
| **SHADOW TRADING NET** | **+3.1267** | **+6.8937** |
| median | +5.4898 | +5.8911 |
| **trimmed mean** | +2.7666 | **+4.9219** |
| lock-hit rate | 10 of 17 | 12 of 17 |
| positive-window rate | 10 of 17 | 12 of 17 |
| worst window | 2030z, −15.3540 | 2245z, −10.6526 |
| best window | 2300z, +27.0088 | 2300z, +54.0176 |
| best window's share of total | 0.5081 | **0.4609** |
| mean excluding best window | +1.6340 | **+3.9485** |
| max peak directional | **50, unchanged** | **100, unchanged** |

Concentration is **easing as n grows**: the best window's share of total P&L has
fallen 0.5464 (n=14) → 0.5216 (n=16) → **0.4609** (n=17), and the mean excluding it
has risen +3.4489 → +3.3027 → **+3.9485**. The trimmed mean has risen to **+4.9219**,
now a hair under $5. Still **TARGET CROSSED — NOT ESTABLISHED**; nothing is
selected, and the trend is reported, not extrapolated.

Residual episodes 7 of 17 (frequency 0.4118, Wilson [0.2161, 0.6399]), 4 lost and
3 won. Break-even frequency is now **1.0513**, i.e. above 1.0 and therefore not a
meaningful bound any more: with before-residual at +12.2908 and a signed mean
episode P&L of only −11.6915, no attainable frequency of episodes would eliminate
the profit. Recorded as an artefact of the mixed-sign episode population, not as an
achievement.

Protocol frozen per `analysis/pmus/CHECKPOINT25_FREEZE.md`. No parameter moved, no
challenger started, nothing selected.
