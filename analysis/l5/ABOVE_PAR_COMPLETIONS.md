# Above-par completions: ten episodes, reconstructed

**Question (owner, 2026-09-11):** a supposedly hard `completion_pair_ceiling = 1.00`
and ten zero-residual episodes with effective pair costs up to 1.085714 cannot
both describe the same execution economics. Reconstruct them action-by-action
and distinguish the classes: average-cost accounting artifact, partial-fill /
multi-session interaction, stale decision-state versus execution, or a genuine
guard escape.

**Answer: none of those four. The class is PRE-GUARD HISTORY.** All ten predate
the ceilings, which both landed on 2026-09-10.

---

## The finding is real, not an artifact

Worst case, `aec-atp-nikizq-santav-2026-09-08`:

```
final inventory  up 13.21  dn 13.21  matched 13.21  residual 0.00
                 spent 14.176900
                 avg_up 0.65062831…  avg_dn 0.42256623…
```

`avg_up + avg_dn = 1.073195`, and independently `spent / matched =
14.1769 / 13.21 = 1.073195`. Because `up == dn` the two agree exactly, so this
is **not** an averaging artifact. The pair genuinely cost \$1.0732 and pays
\$1.00 whichever side wins: **−\$0.967 locked in**.

## The action-by-action reconstruction

```
session 1  14:26:27Z  SPRING_RESIDUAL_HOLD
  open      NO  5      filled 5     @ 0.49        inv 0/5
  complete  YES 5      filled 0
  complete  YES 3.78   filled 3.78  @ 0.66        inv 3.78/5
    -> avg_up 0.66 + avg_dn 0.49 = 1.15  ALREADY ABOVE PAR
session 2  14:27:42Z  SPRING_RESIDUAL_HOLD
  extend    NO  4.31   filled 4.31                inv 3.78/9.31
  extend    NO  3.90   filled 3.90                inv 3.78/13.21
  complete  YES 3.62   filled 3.62                inv 7.40/13.21
session 3  14:28:20Z  SPRING_MATCHED
  complete  YES 4.03   filled 4.03
  complete  YES 1.78   filled 1.78                inv 13.21/13.21
```

Two things matter here, and the second is counter-intuitive:

1. **The damage was done at the first COMPLETE.** Paying 0.66 for YES against
   a NO leg averaging 0.49 put the pair at 1.15 immediately, on 3.78 matched
   units. Nothing later created the loss.
2. **Every subsequent action reduced it.** The NO extends came in around 0.38
   (pulling `avg_dn` 0.49 → 0.4226) and the later YES completes came in below
   0.66 (pulling `avg_up` 0.66 → 0.6506). The engine dug *out* from 1.15 to
   1.0732. The residual-side extends were repairing the average, not damaging
   it — the opposite of the simulated loophole that motivated the guard.

No decision in any session carries `matched_pair_cost_if_filled`, which is set
only on refusal. Nothing refused, because at the time nothing could.

## Why nothing refused: both ceilings landed on 2026-09-10

| path | mechanism | landed |
|---|---|---|
| OPEN / EXTEND | `pair_invariant_ok` — bounds the **average after fill** | `2ad10cdc`, 2026-09-10 02:00:46Z |
| COMPLETE | its own branch: `econ_max = ceiling − avg_cost`, price floored to it | 2026-09-10, superseding "pair reported not gated" |

The COMPLETE cap's own comment names the evidence that produced it:
`asc-mlb-chc-mil-2026-09-09` followed the touch to 0.880 against a first leg
averaging 0.3612 — a pair at **1.2412** — "losing in EVERY branch".

Every one of the ten above-par markets is dated 2026-09-07, -08 or -09.

## Before and after, measured

```
PRE-guard  (slug date < 2026-09-10):  50 zero-residual pairs, 10 above par,
                                      max 1.085714
POST-guard (slug date >= 2026-09-10):  4 zero-residual pairs,  0 above par,
                                      max 1.000000
```

The post-guard sample is **four markets** — consistent with enforcement, not by
itself conclusive. The weight of the live-safety conclusion rests on the other
three lines of evidence below, not on this count.

The ten, with the derived loss where it can be computed:

| market | matched | pair cost | derived |
|---|---:|---:|---:|
| `aec-cs2-mouzn-bmb-2026-09-08` | — | 1.085714 | — |
| `aec-atp-nikizq-santav-2026-09-08` | 13.21 | 1.073195 | −0.9669 |
| `aec-lol-dnsc-nsea-2026-09-09` | 16.90 | 1.052651 | −0.8898 |
| `aec-cs2-g2-ast-2026-09-08` | — | 1.044005 | — |
| `atc-ucl-lil-rbb-2026-09-08-draw` | 10.30 | 1.040621 | −0.4184 |
| `aec-wta-elepri-aneolm-2026-09-07` | 16.45 | 1.027404 | −0.4508 |
| `aec-itfwo-gindit-saverl-2026-09-08` | — | 1.022794 | — |
| `aec-setkamecz-simjak-jupjac-2026-09-08` | 10.10 | 1.010257 | −0.1036 |
| `aec-itfwo-sopgre-matpao-2026-09-08` | — | 1.008937 | — |
| `aec-wta-martre-jenrug-2026-09-08` | — | 1.000191 | — |

At least **−\$2.8295** across the five with a computable matched quantity.

## Live safety determination

The owner's condition: do not stop the probe for historical archaeology **if
the currently deployed path demonstrably enforces ≤1.00 on every new
completion**; otherwise new completion orders fail closed.

Enforcement is established on three independent lines:

1. **Source.** Both caps are present and on the submit path, refusing *before*
   submission. `pair_invariant_ok` for OPEN/EXTEND; `econ_max` flooring for
   COMPLETE.
2. **Runtime self-assertion.** `matched_pair_invariant_enforced: true` in the
   boot row of deployment `6cfbd53c` at 12:03:16Z — the process reading its own
   source, not an operator's memory.
3. **Live behaviour today.** `aec-itfwo-kaycro-sofjoh-2026-09-11` completed at
   `pair_at_chosen 0.9900`, `above_par false`. The cricket residual shows the
   cap actively *binding and holding*: `pair_at_touch 1.138536` refused down to
   `pair_at_chosen 0.998536`, `capped_by_pair_ceiling true`.

**Conclusion: the probe continues. No fail-closed.**

### The residual risk, stated

Both caps compute on the session's `Inventory`. If the ledger misses a fill,
the guard reasons over an incomplete book and could pass an order it should
refuse. That is the same defect shape as the market that settled with ledger
residual 0.00 and a locked +0.3318 while the venue's realized was −1.5778. The
mitigating control is the per-session venue-vs-ledger reconciliation, which was
green on today's only completion (`venue 11.2000` vs `ledger 11.2`,
`agrees true`). Enforcement is therefore demonstrable **conditional on
inventory accuracy**, and inventory accuracy has its own live check.

## Changes made

- `_pair_invariant_enforced` now requires **both** caps. It previously checked
  only `pair_invariant_ok`, so it would have reported `true` with the COMPLETE
  cap deleted — and COMPLETE is precisely the leg that produced the 1.2412
  pair. A gate that guards one of two doors is not a gate.
- Two stale docstrings saying the pair is "reported not gated" were corrected.
  They were true of `l5_touch` for one day and false afterwards, and reading one
  of them is what briefly persuaded this investigation that the live path had no
  completion ceiling. A stale label on a safety field is a trap.
- `tests/test_completion_ceiling_both_paths.py` pins all of it, including a test
  that deletes each cap in turn from a copy of the source and requires the boot
  assertion to go false.

Nothing was loosened. No cap, ceiling, budget or bound changed value.
