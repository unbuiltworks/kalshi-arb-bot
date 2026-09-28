# REPO_TESTS_001 — definition, prospective. Two sets, both preserved.

Written 2026-09-28 after the corrected import closure showed that "this test
transitively touches a packaged module" is a badly overinclusive proxy for
"this test exercises the artifact".

## The two sets

**1. BROAD DEPENDENCY SET — 385 test files.** Every test that transitively
touches *any* of the 45 packaged files, package markers included.

**2. ARTIFACT-BEHAVIOUR SET — 257 test files.** Every test that reaches a
packaged module which actually **carries behaviour**. This is the certification
set.

**Overinclusion: 128 files** — in the broad set only.

## Why the gap is 128 files, not noise

`src/__init__.py` is **0 bytes**. It must be in the image — omit it and every
`from src import x` fails — but it defines nothing. Under the broad relation,
*every* test that says `from src import anything` therefore "reaches the
artifact". That is shared package ancestry, and it is the whole reason the set
jumped 249 → 385 when relative imports were finally resolved.

`src/l5_import_closure.behaviour_bearing()` draws the line mechanically: a module
carries behaviour unless its entire body is a docstring, `__all__`, and imports.
The two packaged files that fail that test are named explicitly in every report:

```
src/__init__.py                  (0 bytes)
src/polymarket_l5/__init__.py    (docstring + __all__)
```

Both stay in the closure for packaging. Neither may ever be the reason a test is
called artifact-relevant.

## The certification rule

* **Every test in the ARTIFACT-BEHAVIOUR set must pass. Zero failures, zero
  errors.** No exceptions, no waivers.
* A **broad-set** failure may be excluded **only** with a named, mechanically
  demonstrated unreachable-behaviour proof — the import chain, printed.

## The vocabulary, and the word that is now banned

| verdict | meaning |
|---|---|
| `ARTIFACT_BEHAVIOUR` | reaches a behaviour-bearing packaged module. Blocker until shown otherwise. |
| `TEST_GRAPH_OVERINCLUSION` | reaches the closure **only** through behaviour-free package markers. Repository debt. |
| `DISJOINT` | no import path into the closure at all. |

**"Disjoint" is never again used for a test that reaches the closure but whose
failing subsystem is not packaged.** That was the error in
`PROBE_ARTIFACT_REVIEW_8039c416.md` §5: nine files were called "transitively
disjoint" when four of them reached the closure and the computation that said
otherwise was itself broken. The correct word for those four is
`TEST_GRAPH_OVERINCLUSION`, and it comes with a proof.

## One implementation, because three were the bug

`src/l5_import_closure.py` is the only closure implementation. Previously there
were three — a shell script, a test helper, and an ad-hoc scrap — and two of them
filtered on `ast.ImportFrom.level == 0`, silently skipping every relative import.
The COPY list was generated from one and verified against the other, so
"byte-identical" was simultaneously true and worthless, and the image shipped
without `src/polymarket_l5/config.py`.

The module is **not** imported by `scripts/l5_pmus_live_oneshot`, so it is tooling
about the capability graph rather than part of it.

`test_the_local_helper_and_the_shared_module_agree` pins the test file's own
independent reading against it. If they ever diverge, one has a blind spot — which
is precisely the condition that shipped an unrunnable image.

## The tool

```
python3 scripts/repo_tests_001.py                  # both counts + named markers
python3 scripts/repo_tests_001.py --json           # full membership
python3 scripts/repo_tests_001.py --list-behaviour # the certification set
python3 scripts/repo_tests_001.py --proof <test>   # verdict + import chain
```

## Regression tests

In `tests/test_lock5_entrypoint_composition.py`:

* the local helper and the shared module must return the same closure;
* the two package markers must be exactly the behaviour-free set, and
  `src/__init__.py` must be 0 bytes **and still packaged**;
* an overinclusion verdict must come with a path proof;
* **negative control** — a genuinely packaged module must classify as
  `ARTIFACT_BEHAVIOUR`, so the classifier cannot pass by calling everything
  overinclusion;
* the forensics behaviour must stay unreachable from the entrypoint.

## Test-environment dependencies — NOT image dependencies

The container was missing three packages, which produced 15 of the 15
failures/errors originally reported as probe-relevant:

| package | installed for tests | in the Rung-A image? |
|---|---|---|
| `httpx` | yes | **no** — no packaged file imports it |
| `websockets` | yes | **already there** (`websockets>=12`) |
| `python-dotenv` | yes | **no** — no packaged file imports it |

With those present, `test_gemini_request_transport` (9 errors), 
`test_gemini_settlement_observability` (2 failures), the two
`test_src_main_*` deploy-guard failures, and both previously uncollectable
Gemini probe files all **pass**. They were environment casualties, not defects.
Nothing was added to the image; corrected reachability does not require it.
