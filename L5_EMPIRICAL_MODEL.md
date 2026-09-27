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
