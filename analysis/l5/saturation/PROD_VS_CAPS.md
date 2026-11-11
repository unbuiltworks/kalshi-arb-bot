# Would the new rule have changed the trades that lost our money?

**Yes — 43.3% of our actual live EXTEND orders, at cap35. Production's own cap
never blocked a single one.**

Nothing was changed in production to establish this.

## Production's cap, replayed faithfully

`pmus_spring_l5_session.py:3085` refuses when the residual **already held** is
`>= max_residual_units()` — a *pre-action* test — and it applies **only to
EXTEND**, leaving COMPLETE allowed and flattening nothing. That is already the
semantics the new hypothesis prescribes; only the threshold differs. The replay
was given those exact semantics (pinned by a test, because the default
post-action test bites one fill earlier and would have misattributed every
difference at the edge).

At the owner calibration of 1 behavioral unit = 10.36 L5 shares:

| arm | cap units | cap L5 shares | ~$ at the p50 fill price 0.44 |
|---|---|---|---|
| **CURRENT PROD** | **6** | **62.16** | $27.35 |
| cap50 | 4.83 | 50.00 | $22.00 |
| cap35 | 3.38 | 35.00 | $15.40 |
| cap20 | 1.93 | 20.00 | $8.80 |

So the first half of the hypothesis is **confirmed**: cap 6 ≈ 62 shares, above
the 20–50 saturation band.

## On L5's BTC15m OOS sequences, production is already on the right side

| arm | matched | residual | net | capital | bp/$ | max DD | worst window |
|---|---|---|---|---|---|---|---|
| CURRENT PROD (6u) | +$19,233 | −$10,956 | +$8,277 | $2,374,046 | 35 | −$428 | −$148 |
| cap50 | +$17,279 | −$9,114 | +$8,165 | $2,246,587 | 36 | −$414 | −$129 |
| **cap35** | +$15,657 | −$6,685 | **+$8,971** | $1,999,931 | **45** | **−$257** | −$106 |
| cap20 | +$11,613 | −$4,552 | +$7,061 | $1,496,136 | 47 | −$240 | −$79 |
| CONTROL exact-L5 | +$89,028 | −$90,452 | **−$1,424** | $3,232,181 | **−4** | **−$8,497** | −$290 |

The large gap is between **unbounded L5 and any cap at all** (−4 bp → +35 bp),
and production is already across it. Moving from cap 6 to cap35 is an
incremental gain — 35 → 45 bp/$, drawdown −$428 → −$257 — not a repair of a
broken policy.

On these sequences alone the honest read would be "mostly cosmetic".

## But our own live campaigns say the opposite

1,704 distinct live decision records, 99 markets, recovered from the probe's own
`session_end` rows. Each carries `lookup.residual_shares_behavioral` — the
residual in **exactly the replay's unit**, at decision time, from production
itself.

Of those, **480 were EXTEND** decisions. Their residual at the moment of the
extend:

| p50 | p75 | p95 | **max** |
|---|---|---|---|
| 29.1 | 45.5 | 56.2 | **59.2** |

| arm | cap shares | live EXTENDs blocked | % of EXTENDs | notional refused |
|---|---|---|---|---|
| **CURRENT PROD (6u)** | 62.2 | **0** | **0.0%** | $0.00 |
| cap50 | 50.0 | 65 | 13.5% | $52.51 |
| **cap35** | 35.0 | **208** | **43.3%** | $182.87 |
| cap20 | 20.0 | 310 | 64.6% | $268.10 |

**Production's cap is set just above the maximum residual it ever actually
reached** — 62.2 against a high-water mark of 59.2 — so on EXTEND it is
effectively non-binding. It has never stopped an extend in this data.

Meanwhile production extended at a median residual of **29.1 shares** and routinely
ran to 45–59: straight through the saturation zone where repair probability had
already plateaued at ~94% and the attained pair stopped improving.

**So this is the second of the two outcomes, not the first.** The rule is not
cosmetic. It targets behaviour we actually paid for.

### What is NOT claimed

$182.87 at cap35 is **notional refused, not P&L saved**. Whether refusing those
208 orders would have improved realized P&L requires each market's settlement
counterfactual, which is not computed here. A refused extend changes the
inventory the market settles against, and that chain is not established.

The `residual_cap` refusals production *did* log (195 `cap_binding`, 190
`residual_cap:fallback_complete`) are a different path — a fallback into
COMPLETE — not an EXTEND block, and they are not counted above.

## Reading

* cap 6 ≈ 62 shares: **confirmed**.
* "almost none of our losing live actions would have changed": **refuted** at
  cap35 and cap20, **supported** at cap50 (13.5%).
* The gap between production and the saturation hypothesis is entirely in the
  **20–50 share band**, which production permits freely and which the corrected
  replay says buys no additional repair probability and no cheaper pair.
* The single highest-value change is therefore **lowering the threshold into the
  saturation band**, not restructuring the engine. Production already has
  continuous inventory management, EXTEND/COMPLETE, maker-only, the $1.00 pair
  ceiling and a correctly-shaped residual gate. Only the number is wrong.

**No production change is made or proposed here.** The prospective four-arm
shadow remains the route, and the comparison comes before any threshold moves.
