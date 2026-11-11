# Streak shadow, and where the fidelity actually collapses

**Nothing here changed production.** `f7a3e224` / deployment `86eb63ff` is still
serving, 160 recorder seats, watch limit 160, financial envelope untouched. The
probe's `watchPatterns` do not include `src/`, so the pushes in this cycle
return SKIPPED on the probe by construction, not by luck.

Two corpora, and they answer different questions:

| corpus | n | what it is | what it is good for |
|---|---|---|---|
| **durable** | **10,679 decisions** | the engine's own `fidelity_gate` rows, computed in-process over the durable ledger and logged | the MAGNITUDE of the collapse |
| **8-hour slice** | **650 decisions, 37 markets, 13:00–21:10Z** | every decision Railway retention still holds, one shell version | the MECHANISM, decision by decision |

---

## P1 — the 0.0064 → 0.4675 collapse, decomposed

Latest durable measurement, 2026-09-13T21:23Z, n = 10,679:

| | complete | extend | open | hold |
|---|---|---|---|---|
| L5 expects | 0.3071 | 0.4550 | 0.2379 | — |
| we SAMPLE | 0.3100 | 0.4518 | 0.2382 | — |
| we ACT | 0.5066 | 0.2056 | 0.2193 | 0.0685 |

`l1_sampled_vs_l5 = 0.0064` — **the model is L5.** `l1_acted_vs_l5 = 0.4675`.

That 0.4675 is not diffuse. It is two numbers:

| component | contribution | share of the L1 |
|---|---|---|
| **EXTEND deficit** (0.2056 vs 0.4550) | **0.2494** | **53.3%** |
| **COMPLETE excess** (0.5066 vs 0.3071) | **0.1995** | **42.7%** |
| open | 0.0186 | 4.0% |

We take **2,196** extends where L5's own distribution asks for **4,825**. The
missing ~2,600 extends do not vanish: they come back as completes and holds.

### Override reasons — counts, and the owner's taxonomy

Holds over the durable corpus, by the gate that produced them (732 = 6.85%):

| gate | n | % of holds | owner's category |
|---|---|---|---|
| off_distribution_hold | 204 | 27.9% | none of the eleven (model state) |
| **entry_band_left** | **179** | **24.5%** | none of the eleven — see below |
| combined_loss_stop | 103 | 14.1% | residual/risk |
| complement_not_priceable | 88 | 12.0% | no legal maker price |
| spend_cap_reached | 80 | 10.9% | opening budget |
| unmatched_usd_cap | 62 | 8.5% | residual/risk |
| complete_without_residual | 16 | 2.2% | inventory state |

Six of the owner's eleven categories never fire at all: **pair ceiling 0,
fee-safe 0, post-only 0, stale evidence 0, settlement horizon 0, order/rest
limit 0.** That is a finding, not an empty row: the economic gates we spent the
most design effort on are not what is suppressing L5 behaviour. Two of the seven
gates that DO fire — `off_distribution_hold` and `entry_band_left`, together
52.4% of all holds — are not in the taxonomy at all, because neither is an
economic refusal.

`entry_band_left` is not in the taxonomy because it is not an economic gate. It
is an ENTRY-admission price band (the bid leaving 0.25–0.75 / q1 0.20–0.80)
applied to decisions that are frequently not entries.

### The conversion matrix — 8-hour slice, n = 650

Sampled → acted. Our draw reproduced the engine's own draw on **650 of 650**
decisions, so this matrix is the shell's doing and not a sampling artefact:

| sampled ↓ / acted → | open | extend | complete | hold |
|---|---|---|---|---|
| **open** | 197 | 0 | **4** | **1** |
| **extend** | 0 | 213 | **72** | 0 |
| **complete** | 0 | 0 | 163 | 0 |

77 conversions (11.85%), and every one of them lands on COMPLETE (or, once, on
HOLD). Nothing ever converts INTO an extend. By gate:

| gate | n | % of overrides |
|---|---|---|
| entry_band_left | 75 | 97.4% |
| residual_cap | 2 | 2.6% |

The current shell no longer HOLDS on `entry_band_left` while holding inventory —
it converts to completion-only work (that was the R5FT fix). So the durable
corpus shows the collapse as holds + conversions; the current corpus shows it
almost entirely as conversions. Same mechanism, different exhaust.

### And the substitution is not economically neutral

Fill rates over the 582 joined orders in the slice:

| acted relation | orders | filled | fill rate |
|---|---|---|---|
| **extend** | 135 | 19 | **14.07%** |
| complete | 236 | 16 | 6.78% |
| open | 191 | 9 | 4.71% |
| — complete the model chose | 162 | 13 | 8.02% |
| — **complete the shell forced** | 74 | 3 | **4.05%** |

The override replaces our highest-filling action with our lowest-filling one,
and the forced completions fill at about half the rate of the chosen ones.
**Caveat, stated plainly: 3 fills out of 74 versus 13 out of 162 is not
statistically separable.** The direction is consistent with everything P0-B and
P0-D measured about complement reachability; the magnitude is not established.

---

## P3 — was the overridden action impossible, or just refused?

| class | n | % |
|---|---|---|
| **B — executable; OUR implementation policy refused it** | **75** | **97.4%** |
| A — genuinely impossible or unsafe | 2 | 2.6% |
| C — unknown for want of evidence | 0 | 0% |

The two A cases are `residual_cap`: a declared owner risk bound, correctly
enforced. The 75 B cases are `entry_band_left`, which records a BID outside an
admission band — not a missing maker price, not a pair-ceiling breach, not a
venue rejection. Nothing in those records states a venue-side impossibility.

**B does not mean the band is wrong.** It means the constraint is ours, so it is
ours to examine, and it is the single largest lever on fidelity that exists. The
prior band study measured the split and it is not symmetric: relaxing the band
for COMPLETE was +7.69, for EXTEND −2.74 (fill-assumed). Deleting it would buy
deeper into markets that are moving away from us. This report proposes no change
to it.

---

## P2 — does the streak arm earn promotion? No.

Both arms fitted on training windows only (before 2026-08-31) and scored on
14,430 held-out transitions. The production artifact pools train+valid — correct
for serving, useless for judging — so nothing here loads it.

| metric | FROZEN | STREAK | delta |
|---|---|---|---|
| log-loss | 0.59345 | 0.59203 | **+0.239%** |
| Brier | 0.40612 | 0.40528 | +0.207% |
| same-side calibration (ECE) | 0.19212 | **0.19564** | **worse** |
| action-distribution L1 | 0.0081 | **0.0089** | **worse** |
| trajectory L1 (mean / median) | 0.3906 / 0.1677 | 0.3849 / **0.1461** | better |
| accuracy | 0.6856 | 0.6873 | +0.17pp |

Action frequency inside the three cells P2 flagged — the measurement that was
supposed to decide this — is mixed and thin:

| cell | holdout n | frozen freq-L1 | streak freq-L1 |
|---|---|---|---|
| `p<10c\|tte2-5m\|imb10-50` | 91 | 0.1328 | **0.1215** |
| `p10-25c\|tte30-120s\|imb10-50` | 65 | 0.1744 | 0.1769 |
| `p10-25c\|tte2-5m\|imb1-10` | 24 | **0.2224** | 0.2568 |

Better in one, a wash in one, worse in one, at n = 24–91.

### Why P2 looked stronger — a correction I owe

Last cycle I reported side streak as "the consistent omission from the frozen
state key" at +0.68% / +1.59% log-loss. **I tested it against the wrong key.**
P2 scored against the THREE-part key (price band | tte | imbalance). Production
does not serve that key: its live lookup is `base | last_rel | last_size`, which
already conditions on the previous relation. Measured against the key P2 used,
on this holdout:

| key | log-loss | accuracy |
|---|---|---|
| three-part only | 0.68971 | 0.5289 |
| three-part + streak | 0.68825 (+0.212%) | 0.5451 (+1.62pp) |
| **production key** | **0.59345** | **0.6856** |
| production key + streak | 0.59203 (+0.239%) | 0.6873 (+0.17pp) |

Production's own key is worth **13.9% of log-loss** over the three-part key.
Most of what streak was adding in P2, production already had. The variable is
real; the headroom I attributed to it was mostly already spent.

**Recommendation: do not promote the streak arm.** It is built, tested and
parked. On the 8-hour live slice the two arms disagree on 33 of 650 decisions
(5.1%) — a real behavioural difference, but one with no out-of-sample case
behind it, and the two metrics a stochastic engine cares about most (calibration
and action-distribution L1) both move the wrong way.

---

## P4 — a safe offline export, designed and proven, NOT deployed

`src/pmus_ledger_export.py` + 16 isolation regressions.

Chosen architecture: the probe reads its EXISTING ledger file read-only, in
bounded chunks, redacts to an allowlist, and prints the chunks as ordinary log
rows. Research reassembles them from Railway's authenticated log API — the same
path every live decision in this report came through. **No new transport is
introduced at all.**

| requirement | how it is met | proven by |
|---|---|---|
| no HTTP endpoint | no socket is opened | design; no network import |
| no credentials in artifact | allowlist + secret-shaped-value scan drops the whole row | 3 tests |
| no ledger mutation | read-only mode asserted at the call site | open-mode spy + byte-identical file |
| no second executor | one function call on the existing loop | — |
| no volume move | reads in place | — |
| cannot affect execution | total wrapper: any BaseException returns None and retires the exporter for the process | 4 tests incl. MemoryError/KeyboardInterrupt |
| bounded | max rows AND max bytes per call | 2 tests |
| reassembles | forward-walking offsets, duplicate-safe | 2 tests |

Rejected alternatives, so the choice is auditable: **Railway bucket** (puts write
credentials for an external store inside the money-moving service), **CI
artifact** (CI cannot see the probe's volume), **recorder's :8080** (wrong
volume, cross-service), **a probe endpoint** (excluded by the owner).

Default is OFF (`PMUS_LEDGER_EXPORT` unset). A test asserts no armed module
imports it. **Wiring it means a new image on a service that moves money, which
is a separate decision and is not taken here.**

Worth noting: the durable ledger is *already* partially reachable — the engine's
own `fidelity_gate` rows summarise all 10,679 decisions into the log stream, and
that is where this report's P1 magnitude comes from. What is missing is row-level
detail, which is exactly what the exporter would provide.

---

## P5 — FILLED → SETTLED → REALIZED

Venue-confirmed position resolutions, commit `f7a3e22410a3`, settlement-only
labelling:

| market | realized USD | confirmed |
|---|---|---|
| `aec-setkameua-sapole-salvas-2026-09-13` | **−1.0779** | 18:54:31Z, beforeNet −3.4900 → afterNet 0 |
| `aec-nfl-buf-hou-2026-09-13` | **−2.5896** | 20:28:17Z, market EXPIRED / resolved |
| `aec-mlb-lad-mia-2026-09-13` | **+6.0034** | 20:47:38Z, beforeNet −4.0500 → afterNet 0 |
| **net** | **+2.3359** | |

**BUF-HOU is closed** at −2.5896; the residual was held to settlement as
policy required.

Credited incentives in the audited window, venue-credited only: **liquidity
reward payouts $4.53** (2 transactions). Separately, a **$10.00 ISV goodwill
credit** for the 2026-09-05 outage — that is neither trading P&L nor an
incentive and is not netted into anything.

Still open: `aec-setkamecz-hanjos-mejjak-2026-09-13` (position 9.46, unresolved)
and `aec-mlb-sea-ath-2026-09-13`.

---

## Provenance

* `l5_action_model_streak.json.gz` — the shadow model; its `full`/`base`/`marginal`
  counts are byte-identical to the production artifact (asserted by test), so
  the streak key is the only difference.
* `live_sessions.jsonl.gz` — 650 decisions with their orders, from Railway logs.
* `live_rows.jsonl.gz` — the P0 three-way log, one row per decision.
* `live_replay.json`, `oos_scores.json`, `fidelity_gate_snapshots.json`.
* `replay_live.py` (P0/P1/P3), `score_oos.py` (P2).
