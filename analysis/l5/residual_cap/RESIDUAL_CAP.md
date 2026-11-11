# What the residual cap is actually buying

**Production unchanged.** `f7a3e224` / `86eb63ff`, **residual cap stays 6**,
160 recorder seats, watch limit 160, envelope untouched. UNBOUNDED is a
diagnostic measurement of suppressed action mass and is not a live
candidate.

**CONTROL validates.** `band=current | cap=6` reproduces production exactly:
0 extra fills, 0 production extends refused, settlement delta **0.0000**.

Each arm keeps **its own inventory** from the first divergence on, seeded
from the position production already held at the first retained decision,
and every later gate is adjudicated on that arm's position. Two consequences
a filter would miss and this models: a counterfactual fill moves the arm's
residual, and **an arm that spends headroom early can refuse an extend
production actually filled** (reported per cell as
`production_extends_this_arm_would_refuse`).

---

## P0/P1 — the 2-D surface (TAPE_SUPPORTED)

| arm | extra fills | capital Δ | **matched contracts Δ** | directional Δ | **total Δ** | peak units (max) | terminal residual (max) | worst market |
|---|---|---|---|---|---|---|---|---|
| band=current, cap=6 *(CONTROL)* | 0 | $0.00 | **0.00** | 0.0000 | **0.0000** | 6.58 | 8.58 | 0.0000 |
| band=current, cap=8 | 2 | $1.27 | **0.00** | 0.0000 | **0.0000** | 8.58 | 11.32 | 0.0000 |
| band=current, cap=10 | 2 | $1.27 | **0.00** | 0.0000 | **0.0000** | 8.58 | 11.32 | 0.0000 |
| band=current, cap=12 | 2 | $1.27 | **0.00** | 0.0000 | **0.0000** | 8.58 | 11.32 | 0.0000 |
| band=current, cap=UNBOUNDED | 2 | $1.27 | **0.00** | 0.0000 | **0.0000** | 8.58 | 11.32 | 0.0000 |
| band=entry-only, cap=6 | 7 | $2.03 | **0.00** | +1.4488 | **+1.5795** | 6.58 | 8.58 | −0.3944 |
| band=entry-only, cap=8 | 12 | $3.74 | **0.00** | +4.0815 | **+4.5549** | 8.58 | 11.32 | −0.3944 |
| band=entry-only, cap=10 | 16 | $4.16 | **0.00** | +7.7252 | **+8.5230** | 10.44 | 15.46 | −0.3944 |
| band=entry-only, cap=12 | 19 | $4.35 | **0.00** | +10.5114 | **+11.4974** | 12.09 | 18.63 | −0.3944 |
| band=entry-only, cap=UNBOUNDED | 30 | $11.25 | **0.00** | +19.9688 | **+20.5740** | 20.41 | 34.60 | −0.3944 |

**Under CURRENT band semantics the cap changes nothing that settles.** The
only decisions it frees are two in `aec-cs2-mzp-ezt`, which is unsettled and
contributes to no economics column. The cap only becomes visible once the
held-inventory band is relaxed — so the two layers are **not** independent:
the band is upstream and gates whether the cap is ever reached.

### The marginal curve (entry-only)

| step | Δ realized | per unit | extra fills |
|---|---|---|---|
| 6 → 8 | +2.9754 | **+1.4877** | +5 |
| 8 → 10 | +3.9681 | **+1.9840** | +4 |
| 10 → 12 | +2.9744 | **+1.4872** | +3 |
| 12 → UNBOUNDED *(diagnostic)* | +9.0766 | — | +11 |

Positive and roughly flat at ~$1.5–2.0 per unit. **And every dollar of it is
one market.**

### The column that decides it: matched contracts Δ = **0.00 at every arm**

Not one extra contract of matched inventory, at any cap, in any settled
market. lad-mia's matched is 6.08 in production and 6.08 in every arm;
sapole-salvas 2.79 and 2.79. The entire curve above is **directional
residual P&L**.

This is arithmetic, not a modelling choice: `matched = min(up, dn)`, so a
contract added to the side already held cannot become matched unless the
opposite side later exceeds the quantity that side held at the time. It
never did.

*(Note on the matched-pair P&L line in the JSON: it moves slightly even at
constant matched quantity, because buying cheap extra inventory lowers that
side's average cost and the split is taken at averages. Contracts have no
such artifact, which is why contracts is the number quoted here.)*

---

## P2 — risk and return tails

| arm | peak residual units p90 / max | terminal residual p90 / max | median settlement Δ | worst market |
|---|---|---|---|---|
| CONTROL | 3.87 / 6.58 | 4.05 / 8.58 | 0.0000 | 0.0000 |
| entry-only, cap=6 | 4.18 / 6.58 | 6.87 / 8.58 | ~0 | −0.3944 |
| entry-only, cap=8 | 4.18 / 8.58 | 6.87 / 11.32 | ~0 | −0.3944 |
| entry-only, cap=10 | 4.18 / 10.44 | 6.87 / 15.46 | ~0 | −0.3944 |
| entry-only, cap=12 | 4.18 / 12.09 | 6.87 / 18.63 | ~0 | −0.3944 |
| entry-only, UNBOUNDED | 4.18 / 20.41 | 6.87 / 34.60 | ~0 | −0.3944 |

Max terminal residual grows **8.58 → 34.60 contracts** while the median
market is untouched — the extra capacity is consumed entirely by the tail of
one market. Max drawdown contribution is the same market both ways: had
lad-mia's DOWN side lost instead of won, the cap-12 arm would have carried
18.63 contracts of it to settlement instead of 4.05.

**Productive inventory or larger directional gambling?** On this evidence,
the second. Additional capacity produced zero matched inventory and moved
only the directional tail.

---

## P3 — what happened after each extra EXTEND

| class | cap=6 | cap=8 | cap=10 | cap=12 | UNBOUNDED |
|---|---|---|---|---|---|
| **A** later opposite fill created matched inventory | **0** | **0** | **0** | **0** | **0** |
| **B→C** opposite opportunity appeared, no supported fill, settled directional | 7 | 10 | 14 | 17 | 28 |
| **C** remained directional, no opposite opportunity | 0 | 0 | 0 | 0 | 0 |
| **D** evidence ends before outcome | 0 | 2 | 2 | 2 | 2 |

**Zero class A at every cap.** Opposite-side opportunities did appear — the
engine posted 121 COMPLETE orders in lad-mia alone — and none of them filled
in a way that converted the extra inventory. No subsequent fill is invented
anywhere; labels stay OBSERVED / SUPPORTED COUNTERFACTUAL / UNKNOWN.

Against the owner's own test — *"If it simply increases C, reject it"* — it
increases C exclusively.

---

## P4 — interaction with the band

The surface answers the question directly: **the cap appears binding only
because the band is upstream of it.**

* With the band as it is, raising the cap 6 → UNBOUNDED frees **two**
  decisions, both in an unsettled market: **settled economics unchanged at
  every cap**.
* With the band entry-only, the cap becomes the binding layer and each
  additional unit frees 3–5 more extends.

They are not independent, and they are not interchangeable. The band decides
whether an extend is considered at all; the cap decides how many are allowed
once it is. **Changing the cap alone does nothing.**

---

## P5 — L5's own inventory scale, in our units

Normalised by **his own per-window median clip**, so his bankroll is not
imported into our units. 2,216 windows.

| state | n | residual units p50 / p75 / p90 / max | share ≥ our 6-unit cap | P(next fill same side) |
|---|---|---|---|---|
| **p25-45c \| tte>10m \| imb10-50** | 1,413 | 2.31 / 3.50 / 4.48 / 9.02 | **2.8%** | 0.677 |
| **p45-55c \| tte>10m \| imb10-50** | 1,145 | 2.30 / 3.27 / 4.36 / 9.05 | **2.4%** | 0.676 |
| p<10c \| tte>10m \| imb10-50 | 15 | 2.47 / 4.40 / 4.70 / 4.91 | 0.0% | 0.533 |
| p25-45c \| tte>10m \| imb>50 | 3,554 | 12.28 / 18.97 / 25.46 / 73.5 | 84.7% | 0.726 |
| p10-25c \| tte>10m \| imb>50 | 1,936 | 15.61 / 23.05 / 30.54 / 124.9 | 92.4% | 0.724 |

Window level: **terminal residual p50 2.00 units**, p75 4.14, p90 14.98.
**Matched fraction of inventory: p50 0.623**, p90 0.958.

### Is 6 units truncating the oscillation L5 uses?

**In the states we actually occupy, no.** 62 of our 75 band decisions sat in
`imb10-50`, and there L5's own residual is **p50 2.3 units** with only
**2.4–2.8% of his decisions at or above 6**. Our cap is roughly his p95 in
those states. It is not the thing standing between us and his process.

The `imb>50` states are where he runs 12–16 units — but those are states we
reach in 4 of 75 decisions, and "imb>50" is the large-imbalance bucket by
definition, so this is close to circular.

**The real difference is not the size of the residual, it is what happens to
it.** L5's inventory ends **62% matched at the median window**. Our extra
inventory ended **0% matched at every cap**. He oscillates; we accumulate.
Raising the cap lets us accumulate further, not oscillate.

*(And per instruction: none of this infers a PMUS-safe risk level from L5's
larger bankroll.)*

---

## P6 — durable ledger access

**Not available through the existing authorized connection.** The Railway
MCP surface has no volume-file read (`list-/create-/update-/delete-volume`
manage the volume object, not its contents). The one MCP tool that might
reach it is `railway-agent`, which "can take actions on your behalf" against
a project containing the money-moving probe — **I will not point an
action-taking agent at that project for a research read.**

Checked and confirmed absent in this environment: `RAILWAY_TOKEN`,
`RAILWAY_API_TOKEN`, `RAILWAY_PROJECT_TOKEN`, and any `~/.railway` /
`~/.config/railway` credential. **No token created, no secret read.**

**Exactly what authorization is required:** a Railway account or team token
with read access to project `50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`, present
in this research environment as `RAILWAY_TOKEN` (or an interactive
`railway login`). With it, the read is one command and touches nothing:

```
railway volume files download /state/pmus-spring/ledger.jsonl ./ledger.jsonl
```

Until then: **leaving it alone. The stdout exporter stays undeployed**, per
instruction, because the control-plane read path exists and is strictly
better.

---

## P7 — live, and the decision

Unchanged: `f7a3e224`, residual cap 6, 160 seats, watch limit 160, existing
envelope. No new settlements since 20:47Z. Trading result stands at
**+2.3359** across three settled markets; **liquidity rewards $4.53** and
**ISV goodwill $10.00** are reported separately and netted into nothing. No
scaling is proposed on any of it.

### Against the five conditions for cap > 6

| # | condition | result |
|---|---|---|
| 1 | extra residual produces additional MATCHED inventory | **FAILS — 0.00 matched contracts at every cap; 0 of 30 extras class A** |
| 2 | settled economics improve across multiple independent markets | **FAILS — one market (lad-mia) supplies every positive dollar; the other settled market is −0.3944 at every cap** |
| 3 | downside per additional unit inside the bounded probe philosophy | **FAILS — max terminal residual 8.58 → 34.60 contracts; the tail is the only thing that moves** |
| 4 | fidelity improves | **N/A at current band — the cap frees nothing that settles until the band moves first** |
| 5 | survives fresh realtime shadow | **not reached** |

**→ CAP REMAINS 6.** No change proposed, no realtime shadow requested.

The finding worth carrying forward is not about the cap at all: **the extra
inventory never became matched, and L5's is 62% matched at the median.** The
binding problem is still complement reachability — the same thing P0-B and
P0-D found, now confirmed from a third direction.

## Files

`residual_cap_arms.json` (grid, per-market, P3 detail), `l5_inventory_scale.json`,
`run.py`, `l5_scale.py`, `src/l5_residual_cap_arms.py`,
`tests/test_l5_residual_cap_arms.py` (13 regressions).
