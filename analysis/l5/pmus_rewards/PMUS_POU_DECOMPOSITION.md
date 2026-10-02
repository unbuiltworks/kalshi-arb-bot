# Why 67–89% resolution still loses money — full P&L decomposition

2026-10-02. `38606148` frozen. Nothing altered: strategy, thresholds, fill
rules, recorder and data are untouched. No parameter search. No live orders.
c.97 is **not** selected.

## Answer in one line

**Completed pairs never lose money — the loss is entirely the forced
deadline exit, and the two apparently-profitable windows are tape-truncation
artifacts.**

## 1. Exact reconciliation (q25, all 8 windows)

Per-lot components, signed so they sum to cash:

| ceiling | entry_cash | exit_cash | = cash | locked_edge | residual (open-qty cash) | taker_fee | maker_rebate | net_pre | net_post |
|---|---|---|---|---|---|---|---|---|---|
| .99 | −334.05 | +358.71 | **+24.66** | −51.47 | +76.13 | 78.54 | 19.33 | **−53.88** | **−34.55** |
| .98 | −228.81 | +242.79 | +13.98 | −62.15 | +76.13 | 82.65 | 18.99 | −68.67 | −49.68 |
| .97 | −295.96 | +388.27 | +92.31 | −18.80 | +111.11 | 76.56 | 19.25 | +15.75 | +35.00 |
| .95 | −3.20 | +24.24 | +21.04 | −111.07 | +132.11 | 70.89 | 19.19 | −49.85 | −30.66 |

Two identities hold exactly, to the cent, at every ceiling:

    entry_cash + exit_cash                  = cash
    cash − taker_fee + maker_rebate          = net_after_comp
    locked_edge + residual_open_qty_cash     = cash

**The realised locked edge on closed quantity is NEGATIVE at every ceiling**
(−$18.80 to −$111.07). What makes the headline look better is `residual`:
+$76 to +$133 of cash received on positions **still open**, carried in the
total as though it were profit. It is not — see §4.

## 2. P&L distribution by exit path (q25, ceiling .99)

| bucket | n | sum | mean | median | min | max |
|---|---|---|---|---|---|---|
| **PAIR_TAKER** | **440** | **+102.97** | +0.2340 | +0.1447 | **0.00** | +2.23 |
| **PAIR_MAKER** | **42** | **+8.75** | +0.2084 | +0.1705 | **0.00** | +0.62 |
| **FORCED** | **53** | **−222.92** | **−4.2060** | −2.7121 | **−17.82** | −0.01 |
| UNRESOLVED | 12 | +76.64 | +6.3867 | +7.9786 | −21.46 | +17.57 |

Same shape at every ceiling: PAIR_TAKER min is **0.00** at .99/.98/.97/.95;
FORCED min runs −$17.82 to −$20.98.

## 3. Your specific question, answered

> If the pair satisfied the ceiling after fees when executed, how can that
> completed pair subsequently contribute negative P&L?

**It cannot, and it does not.** Across all four ceilings the minimum
PAIR_TAKER lot P&L is **exactly $0.00** — never negative. The exit condition is
`pair_cost ≤ ceiling AND (1 − pair_cost)·q − fee > 0`, and since
`1 − pair_cost = exit − entry` for a long, the path is only reachable when the
market has already moved in our favour net of fees. Locked gains are **not**
being consumed by any other component.

**The component consuming the P&L is FORCED**, and the mechanism is an
asymmetry the exit rule itself creates:

| | n | mean per lot |
|---|---|---|
| PAIR_TAKER + PAIR_MAKER | 482 (88%) | **+$0.23** |
| FORCED | 53 (10%) | **−$4.21** |

**An 18:1 loss-to-gain ratio per lot.** 10% of lots destroy 2.2× what 88% of
lots create. Because a qualifying exit is *by construction* only available when
profitable, every lot the market moved against is **ineligible for the
qualifying path** and is held to the deadline, where it is crystallised at the
then-prevailing touch. The strategy harvests winners at ~23¢ and crystallises
losers at ~$4.21.

That is "cut the winners, ride the losers" — not as a behavioural failing but
**as a structural consequence of gating the exit on profitability.** The high
resolution rate and the negative economics are the *same* fact: resolution is
high because profitable exits are plentiful, and economics are negative because
the unprofitable residue is precisely what the rule refuses to exit early.

## 4. The 1345z outlier is a tape-truncation artifact

Tape span against each window's T−90 deadline:

| window | span (s) | last sample vs deadline | reached deadline? | FORCED | UNRESOLVED |
|---|---|---|---|---|---|
| 1215z | 806 | +108 | yes | 10 | 0 |
| 1230z | 899 | +90 | yes | 11 | 0 |
| 1245z | 909 | +103 | yes | 5 | 0 |
| 1300z | 913 | +108 | yes | 11 | 0 |
| 1315z | 913 | +107 | yes | 7 | 0 |
| 1330z | 912 | +107 | yes | 9 | 0 |
| **1345z** | **538** | **−270** | **NO** | **0** | **12–16** |
| **0400z** | **50** | **−472** | **NO** | **0** | **0–?** |

**The two positive windows are exactly the two truncated windows.** 1345z has
**zero FORCED exits** because the tape ends 270 s before its deadline, so its
losing lots were never crystallised while its 88 winners were all harvested.
Its UNRESOLVED lots are mostly **short** (sell 0.73, 0.70, 0.61, 0.61, 0.58,
0.65…), so the sale proceeds are booked as positive cash while the position is
still owed, and **`locked` is +0.00 on every one** — zero realised edge.

Of 1345z's +$144.19 at c.97, **+$111.76 is open-position cash**, leaving
+$32.43 closed-only — and even that is one-sided survivorship, since no loser
was ever forced.

**Verdict: concentration/residual accounting artifact, not repeated pair
economics, and not a fill-model bug.** The fill model behaved correctly; the
tape simply stops before the deadline the policy depends on.

## 5. Clean numbers, truncated windows excluded

| ceiling | reported net | open-position cash | closed-only | **6 complete windows only** | net/leg |
|---|---|---|---|---|---|
| .99 | −34.55 | +76.64 | −111.19 | **−136.12** | −0.3115 |
| .98 | −49.68 | +76.64 | −126.32 | **−152.34** | −0.3408 |
| **.97** | **+35.00** | **+111.76** | **−76.75** | **−110.83** | **−0.2485** |
| .95 | −30.66 | +133.03 | −163.68 | **−223.37** | −0.4835 |

**On the six complete windows every ceiling is negative, and all six windows
are individually negative at every ceiling.** c.97's positive sign was entirely
truncation; removing it leaves no configuration positive and no reason to
select any ceiling.

## 6. What this means for the hypothesis

The structural claim survives and is now better understood: PAIR_OR_UNWIND
resolves inventory because PMUS offers a profitable executable exit most of the
time. The economic claim fails for a reason that is now precisely located —
**not spread, not fees, not rebates, not adverse selection on the first leg, but
the 10% of lots the qualifying rule structurally cannot exit.**

Fees are material but not decisive ($78.54 against a −$51.47 locked edge at
.99); rebates are small and positive (+$19.33); PAIR_MAKER is tiny (42 of 482
exits). The loss is one bucket.

This points at an exit-policy question rather than a venue or capacity
question, and I am not proposing a change to the policy here — doing so would
be fitting to this sample.
