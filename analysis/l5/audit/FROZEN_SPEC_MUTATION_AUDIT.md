# FROZEN-SPEC MUTATION AUDIT

**Date:** 2026-10-07 · **Branch:** `claude/august-l5-forensic-replay-788cml`
**Scope:** every fingerprinted frozen-spec artifact in the repository.
**Status:** AUDIT ONLY. **Nothing was repaired.** No existing spec module,
fingerprint, historical artifact, deployment or ledger was modified. The only
file added is the audit harness `tests/test_frozen_spec_mutation_audit.py`,
which mutates in-process module objects inside `try/finally` and restores
them; it writes nothing to disk and arms nothing.

Evidence command:

```
PYTHONPATH=. python3 tests/test_frozen_spec_mutation_audit.py    # JSON report
PYTHONPATH=. python3 -m pytest tests/test_frozen_spec_mutation_audit.py -q
# -> 5 passed
```

---

## 1. The defect, stated precisely

The derivation helper shared across the EXEC-1 lineage is:

```python
def _apply(spec: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = spec
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value          # <-- DIFF's OWN object, not a copy


def build() -> Dict[str, Any]:
    spec = copy.deepcopy(PARENT_SPEC)        # parent is protected
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)          # DIFF is NOT
    return spec
```

`build()` deep-copies the **parent**, so the parent spec is safe. It does not
copy the **DIFF values**. Every nested mutable value in `DIFF` is therefore
aliased into the built `SPEC`:

```
SPEC[<diff path>] is DIFF[<diff path>]   ->  True
```

A caller who takes what looks like a copy — `dict(SPEC)`, a shallow copy, or
simply a reference handed out as `pre["wire_serialization"] = X.SPEC[...]` —
can reach through and edit the frozen `SPEC` **and** the module-level `DIFF`
literal in place.

`analysis/l5/stage6/spec.py` (STAGE-6-S6P) fixed this for itself, one line:

```python
    node[parts[-1]] = copy.deepcopy(value)
```

The defect is real and is present in five earlier specs. **The exploitability
claim in the original report is NOT reproduced**, and the reason matters — see
§3.

---

## 2. Verdict table

| # | Artifact | Fingerprint | Pattern present | Fingerprint form | Mutation reaches frozen SPEC | `assert_frozen()` after mutation | `diff_report()` corrupted | Real path mutates it | Deployed service on it | **Verdict** |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **EXEC-1** `analysis/l5/live_exec/spec.py` | `b1dbe7f1…2e06` | no — root spec, **no `DIFF`** | hard-coded literal | n/a | n/a | n/a | no | no | **NOT-VULNERABLE** |
| 2 | **EXEC-1-X** `analysis/l5/live_exec2/spec.py` | `d2f32276…6c79` | **YES** — `_apply` assigns `value` | hard-coded literal | **yes** | **FAILS (fail-closed)** | **yes** | no | no | **VULNERABLE-BUT-INERT** |
| 3 | **XR** `analysis/l5/live_exec2/runner_spec.py` | `f3e54d82…c5e9` | **YES** | hard-coded literal | **yes** | **FAILS** | **yes** | no | no | **VULNERABLE-BUT-INERT** |
| 4 | **XR2** `analysis/l5/live_exec3/spec.py` | `0f334019…042f` | **YES** | hard-coded literal | **yes** | **FAILS** | **yes** | no | no | **VULNERABLE-BUT-INERT** |
| 5 | **XR2W** `analysis/l5/live_exec5/spec.py` | `ac6318d5…2562` | **YES** | hard-coded literal | **yes** | **FAILS** | **yes** | no | one-shot, restart `NEVER`, already run (A6) | **VULNERABLE-BUT-INERT** (highest-consequence) |
| 6 | **XR3** `analysis/l5/live_exec4/spec.py` | `6e7f5aed…99a4` | **YES** | hard-coded literal | **yes** | **FAILS** | **yes** | no | no (placeholder owner key, non-authorizable) | **VULNERABLE-BUT-INERT** |
| 7 | **STAGE-6-S6P** `analysis/l5/stage6/spec.py` | `32360e4c…96d8` | **no — `copy.deepcopy(value)`** | hard-coded literal | no (0 aliased paths) | n/a | n/a | no | no | **NOT-VULNERABLE (fixed)** |
| 8 | **S3** `analysis/l5/s3/spec.py` | `8b20a259…87df` | no — literal `SPEC`, no `DIFF` | hard-coded literal | n/a | **FAILS** on direct mutation | n/a | no | no | **NOT-VULNERABLE** |
| 9 | **PMUS fee schedule** `src/pmus_fee_schedule.py` | `ecc240bd…1629` | no — literal `SCHEDULE`, no `DIFF` | hard-coded literal | n/a | **FAILS** on direct mutation | n/a | no | read by shadow/audit paths, read-only | **NOT-VULNERABLE** |
| 10 | **LIVE-L5-MICRO-1** `analysis/l5/live_micro/spec.py` | `0522b0da…1b31` | no `DIFF` | hard-coded literal | n/a | **FAILS** | n/a | no | no | **NOT-VULNERABLE** |
| 11 | **PROBE-1 (historical)** `analysis/l5/live_micro/spec_probe1_frozen.py` | `69904fd4…9118` | no `DIFF` | hard-coded literal | n/a | refuses to run at all (historical record) | n/a | no | no | **NOT-VULNERABLE** |
| 12 | **PAIRCOST** `analysis/l5/paircost/spec.py` | `e9089554…3b10` | no `DIFF` | hard-coded literal | n/a | **FAILS** | n/a | no | no | **NOT-VULNERABLE** |
| 13 | **RESIDUAL-FRONTIER** `analysis/l5/residual_frontier/spec.py` | `4b16c92b…074d` | no `DIFF` | hard-coded literal | n/a | **FAILS** | n/a | no | no | **NOT-VULNERABLE** |
| 14 | **Gemini envelope** `src/gemini_execution/live_envelope.py` | `b3bff654…aa17` (driver `WC.envelope_sha()`) | no — `@dataclass(frozen=True)`, scalar `Decimal` fields, `sha()` over `as_dict()` of strings | runtime-computed, compared against `AUTHORIZED_ENVELOPE` literal | **no** — fields cannot be reassigned, and `as_dict()` returns a fresh dict of `str` | n/a | n/a | no | `gemini-btc15m-shadow` etc. run on it, read-only | **NOT-VULNERABLE** |
| 15 | **Gemini live-probe spec** `src/gemini_execution/live_probe_spec.py` | per-spec `spec_sha` | no — `@dataclass(frozen=True)` | runtime-computed from a frozen dataclass | **no** | n/a | n/a | no | no | **NOT-VULNERABLE** |
| 16 | **Habitat selector** `analysis/l5/habitat/*.json`, `score.py`, `src/l5_habitat_score.py` | — | **no fingerprint / `assert_frozen` exists at all**; frozen by JSON artefact + `SUPERSEDED.json` convention | none | n/a | n/a | n/a | n/a | no | **NOT-VULNERABLE** (but see Rec. H) |
| 17 | `analysis/gemini/info_edge/r2_frozen.py` `_apply` | `ca8152b5…6176` (source sha) | **no** — its `_apply(book, rows, seen, t)` is an order-book applier, an unrelated name collision | source-file sha | n/a | n/a | n/a | n/a | no | **NOT-VULNERABLE** |

Fingerprint integrity across the whole set: **every single fingerprint in the
repository is a HARD-CODED LITERAL compared against a recomputation.** There is
no artifact anywhere in `src/` or `analysis/` whose `assert_frozen()` compares a
value it computed at runtime against another value it computed at runtime. That
is the strictly safer form and it is what saves the lineage here.

**Affected fingerprints (5):**
`d2f32276…6c79` (EXEC-1-X), `f3e54d82…c5e9` (XR), `0f334019…042f` (XR2),
`ac6318d5…2562` (XR2W), `6e7f5aed…99a4` (XR3).

**Not affected (12):** `b1dbe7f1…2e06`, `32360e4c…96d8`, `8b20a259…87df`,
`ecc240bd…1629`, `0522b0da…1b31`, `69904fd4…9118`, `e9089554…3b10`,
`4b16c92b…074d`, `b3bff654…aa17`, the `live_probe_spec` `spec_sha` family, the
habitat artefacts, `ca8152b5…6176`.

### Aliased paths, measured

| Artifact | aliased `DIFF` paths (`SPEC[p] is DIFF[p]`) |
|---|---|
| EXEC-1-X | 2 of 2 mutable: `latch`, `mutation_path` |
| XR | 8 of 8: `entrypoint`, `side_rule`, `placement_rule`, `timing_explicit`, `arming_install`, `cancellation`, `markouts`, `one_submission` |
| XR2 | 3 of 3: `tte_invariant`, `launch`, `entrypoint` |
| XR2W | 6 of 6: `wire_serialization`, `venue_error_capture`, `lifecycle_classification`, `entrypoint.call_graph_to_submit`, `fingerprint_pin`, `supersedes` |
| XR3 | 4 of 4: `supervisor`, `authorization`, `two_ledgers`, `entrypoint` |
| STAGE-6-S6P | **0 of 16** |

Every mutable `DIFF` value in the five affected specs is aliased. None is
missed; there is no partially-shared case.

---

## 3. OBSERVED exploitability — the original claim does not hold

The report that opened this audit said the fingerprint assertion *"would STILL
PASS, because the fingerprint is computed over the object as it then stands …
not over an immutable snapshot."*

**Tested on every affected artifact. It does not still pass.** Observed, per
`audit_one()`:

```
EXEC-1-X   mutation_reached_frozen_spec: true   assert_frozen_passes_after_mutation: FALSE
           SystemExit: LIVE-L5-EXEC-1-X spec moved: expected d2f32276…6c79,
                       got 512c5d5a900bed8fa4781798f94e27ffd2d7d44e42e8e0006d6f…
XR         true / FALSE   got 4c3f0afbbb40659f4beafa424db61245611833ecfc0738bc2f2…
XR2        true / FALSE   got 742c303802fa86ced27e22284cfc1245e0ddd0a3b6026d3a44…
XR2W       true / FALSE   SystemExit: LIVE-L5-EXEC-1-XR2W is not the frozen spec:
                          1153596e83f2c9e5c14f16c4e210a12fa52a3830595cd230dc25d6dd886620b3
                          != ac6318d5fe986f629037edd129e08c70ff0031012070fd10…
XR3        true / FALSE   got 469c48815f5c96fd5401ab19fd77d2a6bbb4ac5059fe1c005d…
```

**Why the claim fails here.** The aliasing is between `SPEC` and `DIFF`, and
`SPEC` is what `fingerprint()` serializes. Because the two names point at *one*
object, any mutation that corrupts `DIFF` necessarily also changes `SPEC`, so
the recomputed sha256 moves away from the hard-coded literal and
`assert_frozen()` raises `SystemExit`. The "still passes" scenario needs a
fingerprint that is *computed and then stored at runtime* rather than compared
against a literal — and **no artifact in this repository does that**. The
S3 spec even documents the reasoning explicitly:

```python
# This is a HARD-CODED LITERAL on purpose. Computing it from SPEC at import
# would make assert_frozen() vacuous -- it would recompute the same value it
# compares against and pass for every possible edit to the spec.
```

The earlier authors' discipline on this one point is what contains the bug.

### What IS silently corruptible — the genuine finding

`diff_report()` reads the module-level `DIFF` literal directly and is covered
by **no assertion at all**. Observed on all five affected artifacts:

```
diff_corrupted_by_mutation: true
diff_report_corrupted:      true      # the injected key appears in keys_added/keys_changed
```

So a mutation through the aliased object:

1. **is caught** by `assert_frozen()` as a fingerprint mismatch, and
2. **permanently corrupts the in-process provenance record** that
   `diff_report()` produces — the artifact the runners print as
   `EXEC1XR_PRE` / `EXEC1XR3_PRE` / `EXEC1XR2W_PRE` and that the deployment
   docs cite as the authoritative "what changed between parent and child".

A fingerprint mismatch is loud; a falsified `keys_added`/`keys_changed` record
is not. In a run where `assert_frozen()` failed and the operator read the
printed diff to understand *why*, the diff would be describing the corruption
rather than the derivation.

### Build-time corruption: checked, absent

A `DIFF` carrying both a whole-dict key `"foo"` and a dotted sub-key
`"foo.bar"` would make `_apply` write into `DIFF`'s own dict at import time.
Tested on all seven lineage modules (`audit_build_time_diff_corruption`):

```
diff_mutated_by_build: false   for EXEC-1-X, XR, XR2, XR2W, XR3, STAGE-6-S6P
```

No `DIFF` in the lineage contains such a key pair. This latent second-order
defect is **not currently triggered**, but it is one `DIFF` key away from being
triggered in any future derivation built on the unfixed helper.

---

## 4. PRACTICAL EXPOSURE — latent, not exercised

Searched all of `src/`, `scripts/` and `analysis/` for any write into a spec
sub-object (`SPEC[...][...] = `, `.update(` on a spec node, shallow `dict(SPEC)`
followed by a nested write). **Result: no code path anywhere mutates a frozen
spec.** All consumers read.

The closest thing to exposure, and it is worth the owner seeing it, is that the
runners hand the aliased objects out **by reference** into their preflight
record:

```python
# scripts/l5_exec1xr2w_run.py
pre["fingerprint_pin_policy"]   = X.SPEC["fingerprint_pin"]
pre["wire_serialization"]       = X.SPEC["wire_serialization"]
pre["lifecycle_classification"] = X.SPEC["lifecycle_classification"]
pre["entrypoint"]               = X.SPEC["entrypoint"]
```

```python
# scripts/l5_exec1xr_run.py
pre["entrypoint"] = X.SPEC["entrypoint"]
# scripts/l5_exec1xr3_supervise.py
"entrypoint": X.SPEC["entrypoint"], "supervisor": X.SPEC["supervisor"],
"two_ledgers": X.SPEC["two_ledgers"],
```

Those are the *same objects* as `DIFF`'s. Today `pre` is only ever
`json.dumps`-ed, so nothing is written back; and in XR2W `X.assert_frozen()`
runs **before** `pre` is assembled, so a pre-existing corruption would have
already refused the run. But one future line that redacted, annotated or
normalised a field of `pre` — a wholly ordinary thing to add to a preflight
record — would be an in-place edit of the frozen spec, performed by the live
runner, inside a money-moving entrypoint, *after* its freeze check had passed.

**Severity classification: LATENT for all five.** None is
VULNERABLE-AND-EXPLOITABLE in the sense of defeating `assert_frozen()`.

---

## 5. Deployed-service exposure

- **XR2W (`ac6318d5…2562`)** is the only lineage artifact ever deployed:
  service `l5-live-probe-oneshot`, project `arb-ev-wallet-shadow`,
  `restartPolicyType: NEVER`, start command `python3 -m
  scripts.l5_exec1xr2w_run`. It is a **one-shot that has already run** (A6 —
  FILLED, settlement unresolved, NOT REALIZED). It is not a long-running
  process, and this audit did not touch it, A6, or its ledgers.
- **XR3 (`6e7f5aed…99a4`)** carries the placeholder owner public key
  (`OWNER_PUBLIC_KEY_NOT_YET_SUPPLIED`), so no signature can verify and it
  stays disarmed by construction. Supplying the real key changes the
  fingerprint anyway. Not deployed.
- **EXEC-1, EXEC-1-X, XR, XR2** are superseded; XR2W's own
  `supersedes.must_not_be_rearmed` names them.
- **No continuously running service imports the affected modules.** Checked
  every `startCommand` under `deploy/*/railway.json`; the long-running
  read-only collectors (`scripts.l5_book_shadow_run`, `src.l5_shadow_loop`,
  `src.gemini_execution.shadow_soak`, `src.pmus_l5_continuous_session`,
  `src.l5_v1_1_shadow_runner`, `scripts.l5_window_collector_run`) contain
  **zero** imports of `analysis.l5.live_exec*` or `analysis.l5.stage6`.
- The running `l5-book-shadow` collector was not touched and is unaffected.

**Nothing currently running is at risk.**

---

## 6. Severity ordering

1. **XR2W `ac6318d5…2562`** — the only artifact that has ever moved money and
   the one named as ACTIVE/deployed. Six aliased paths, including
   `wire_serialization` and `fingerprint_pin`, all handed by reference into the
   live runner's preflight record. Highest consequence if a future edit touches
   `pre`.
2. **XR3 `6e7f5aed…99a4`** — the live-progression candidate; the aliased paths
   are `authorization`, `supervisor`, `two_ledgers`, i.e. precisely the
   authorization surface. It is the spec most likely to be edited next, and it
   would inherit the helper as-is.
3. **XR `f3e54d82…c5e9`** — most aliased paths (8), including `arming_install`,
   `side_rule`, `placement_rule`, `one_submission`.
4. **XR2 `0f334019…042f`** — superseded; aliases `tte_invariant`, `launch`.
5. **EXEC-1-X `d2f32276…6c79`** — superseded; smallest surface (2 paths).
6. *(informational)* The unfixed `_apply` as a **pattern**, propagating into any
   future spec not built on S6P's helper. This is the real risk, because it
   recurs by inheritance and the next derivation may not be as lucky with the
   build-time `"foo"`/`"foo.bar"` collision.

---

## 7. RECOMMENDED REMEDIATION — **NOT APPLIED**

Nothing below has been done. Each needs the owner's approval; the first two are
the only ones that touch frozen artifacts, and both must be fingerprint-neutral
to be acceptable.

**Rec. A — hoist S6P's one-line fix into a single shared helper.** Add
`analysis/l5/spec_derive.py` exporting `apply_diff(spec, dotted, value)` that
does `node[parts[-1]] = copy.deepcopy(value)`, and have **new** specs import it
rather than retyping `_apply`. *Fingerprint-neutral by construction:* deep-copy
changes object identity, never the canonical JSON, so every existing literal
holds. Recommended as the forward fix even if nothing historical is touched.

**Rec. B — for the five affected specs, change `_apply` to deep-copy.**
Verification before merge: re-run every fingerprint and require byte-identical
literals — `b1dbe7f1…2e06`, `d2f32276…6c79`, `f3e54d82…c5e9`, `0f334019…042f`,
`ac6318d5…2562`, `6e7f5aed…99a4`, `32360e4c…96d8` — and the full existing test
suite (`tests/test_live_exec.py`, `test_live_exec2.py`, `test_live_exec1xr.py`,
`test_live_exec1xr2.py`, `test_live_exec1xr3.py`, `test_exec1_wire.py`,
`test_stage6_matched_pair.py`). **For XR2W specifically my recommendation is
NOT to touch it.** It is deployed, independently verified, and carries A6; it
is inert, its one-shot has run, and an edit to a deployed-and-verified artifact
costs more than the latent defect it removes. Prefer Rec. C for XR2W. For the
superseded EXEC-1-X / XR / XR2 the same argument applies with less force — they
are historical records and leaving them is defensible.

**Rec. C — stop handing the aliased objects out by reference (the real fix for
the live path).** In `scripts/l5_exec1xr2w_run.py`, `l5_exec1xr_run.py` and
`l5_exec1xr3_supervise.py`, wrap each embed as
`copy.deepcopy(X.SPEC["entrypoint"])` and so on. This touches **runner
scripts, not frozen specs**, changes no fingerprint, and closes the one
plausible route by which a live runner could ever write into a frozen spec. If
only one recommendation is approved, this is the one with the best
consequence-to-risk ratio.

**Rec. D — make `_apply` structurally incapable of the defect in new work.**
Build the child spec by canonical-JSON round-trip
(`json.loads(canonical_json(parent))` then apply diffs from a round-tripped
`DIFF`). Aliasing becomes impossible rather than merely avoided. Heavier than
Rec. A; worth it for the next money-moving spec.

**Rec. E — assert the absence of aliasing, don't rely on review.** Promote the
harness's `_shared_paths()` check to a standing test: for every spec module
with a `DIFF`, assert `SPEC[p] is not DIFF[p]` for all mutable `p`. This makes
regression of the pattern a red build. `tests/test_frozen_spec_mutation_audit.py`
already contains the check (`test_lineage_aliasing_is_as_documented`) — it
currently asserts the **observed** state, including that the five are aliased,
and would be inverted to assert non-aliasing once Rec. A/B lands.

**Rec. F — cover `diff_report()` with the freeze.** The provenance record is
the thing that can be silently falsified. Either fold a sha256 of
`canonical_json(DIFF)` into the module's own fingerprint, or have
`diff_report()` deep-copy its values on the way out. The second is
fingerprint-neutral and trivial.

**Rec. G — guard the build-time collision.** In `build()`, refuse a `DIFF` that
contains both `"foo"` and any `"foo.*"` key. Currently no `DIFF` does; the check
is three lines and stops the second-order defect from ever being introduced.

**Rec. H — habitat artefacts have no freeze mechanism at all.** The habitat
selector is frozen only by the `SUPERSEDED.json` / JSON-artefact convention,
with no fingerprint and no `assert_frozen()`. That is a gap of a different kind
from this bug and out of scope here, but if habitat ever gates an execution
decision it should carry a hard-coded literal fingerprint like S3 and the fee
schedule do.

---

## 8. Statement of non-interference

- `git status` before commit showed exactly one untracked file,
  `tests/test_frozen_spec_mutation_audit.py`, and no modifications.
- Every fingerprint listed in §2 was recomputed from the live module and
  matched its hard-coded literal both before and after the mutation probes
  (`restored_clean: true` on all seven lineage modules and all standalone
  artifacts except PROBE-1, which refuses to run at all by design as a
  historical record).
- No live order, no arming, no owner value, no credential. XR2W, A6, S3, the
  PMUS fee schedule, `analysis/l5/stage6/` and the running `l5-book-shadow`
  collector were read and not written.
