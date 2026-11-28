# Arming still blocked — second blocker: the R1 lineage review gate

**HALT still present. No `live_mandate.json`. Nothing armed. No order submitted
or submittable.** The frozen 100K V1.1 run is untouched.

Option A is **built and tested**. The remaining blocker is a review gate, not a
limit mismatch.

## What is done and green

| | |
|---|---|
| CP1 rung added (`OWNER_RUNGS`, `promote: None`, off the auto ladder) | ✅ |
| Image rung `R1 → CP1`; market allowance `1 → 3` | ✅ |
| `halted_envelope()` so step 8 is executable before HALT is lifted | ✅ |
| Owner tests 1–3: mandate adversarial · ledger-prefix · deployment | **39/39** |
| Owner tests 4–7 + extras (`tests/test_cp1_probe_rung.py`) | **18/18** |
| `min(rung, mandate)` preserved; `pmus_live_mandate.py` byte-identical | asserted by test |

Effective envelope under CP1 + the authorized mandate:

```
min(rung $5.00, mandate $5.00) = $5.00        worst case/campaign  $15.00
residual_units 3  (<= $3.00 by instrument definition: a contract settles $0/$1)
concurrency 1  ·  markets 3  ·  daily stop -$6.00  ·  k 0.00619 unchanged
```

## The blocker

`analysis/l5/lanes/r1_source_lineage.py` pins the R1 image's COPY set against
reviewed artifact **`6adcc4c4`** and rejects any differing file whose disclosed
class is in `FORBIDDEN_CLASSES = ("EXECUTION", "DECISION", "SIZING",
"SUBMISSION")`.

`src/pmus_l5_ramp_ladder.py` is in the image COPY set and now differs.
**Its honest class is `SIZING`** — the change raises a per-market cap. I am not
willing to label a cap increase as anything else to obtain a green gate.

Currently red (3 of 10 lineage tests):

```
test_the_acceptance_condition_holds_on_the_current_tree
test_every_disclosed_delta_is_named_and_none_is_execution_class
test_editing_an_allow_listed_file_again_fails_until_re_reviewed
undisclosed: ['src/pmus_l5_ramp_ladder.py']   differing: 6 (test asserts 5)
```

The gate is a **review** gate, not a boot gate — nothing checks it at runtime,
so it cannot stop the container. But it fails the suite, and the authorization
said proceed "provided all limits and tests above are satisfied".

## Why I did not resolve it myself

Every route to green changes a safety/review mechanism the authorization did
not cover. It authorized "one new bounded rung/configuration", not an
amendment to the lineage gate.

| | Route | Assessment |
|---|---|---|
| **A2** | Advance `REVIEWED` from `6adcc4c4` to the new candidate artifact | **Recommended.** It is literally what the authorization describes — "the newly tested artifact … becomes the candidate artifact". The gate then protects against drift from the NEW reviewed baseline. Cost: the current disclosure list collapses to empty (all deltas become the baseline); the audit trail survives in git history, not in the file. |
| **B2** | Disclose the ladder as `SIZING` and remove `SIZING` from `FORBIDDEN_CLASSES` | **Not recommended.** It weakens the gate permanently for every future change, to solve one authorized case. |
| **C2** | Add a new non-forbidden class (e.g. `AUTHORIZED_ENVELOPE`) | Only acceptable if the test simultaneously requires such a delta to name a specific owner authorization; otherwise it is B2 with extra steps. |
| **D2** | Leave the gate red and promote anyway | **Not recommended** — it normalises a red acceptance gate on the one image that moves money. |

## Nothing else is outstanding

Steps 1–4 of the activation procedure were verified earlier and still hold;
account re-verified 18:15Z: `SHUTDOWN_VERIFIED`, `held_preserved []`, zero open
orders, flat, `refusals 0`, realized today −$0.0916.

Once the gate question is answered, the remaining sequence is mechanical:
re-pin, full suite, promote, verify serving SHA, read `effective_envelope` from
the halted log, confirm every limit, then remove HALT.
