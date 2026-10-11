# Acquisition-rest test run against the existing baseline

RUN DATE 2026-10-11. Commit: the tip of `claude/btc15m-execution-audit-jjdxce`
with `src/pmus_acquisition_rest.py` and `tests/test_pmus_acquisition_rest.py`
present. `src/pmus_spring_l5_session.py` is UNCHANGED; the patch in
`../acquisition_rest.patch` is still unapplied.

## Isolated

    python3 -m pytest tests/test_pmus_acquisition_rest.py -q
    22 passed in 0.93s

13 test functions, 22 cases after parametrisation.

## Full suite, baseline comparison

Three modules cannot be COLLECTED in this container at all, for a reason that
predates this work and has nothing to do with it:

    tests/test_gemini_collector_windows.py
    tests/test_gemini_position_semantics_probe.py
    tests/test_gemini_settlement_probe.py
    ModuleNotFoundError: No module named 'httpx'

A collection error aborts the whole run, so both runs below exclude those three
by `--ignore`. Both runs used `-p no:randomly` so the comparison is like for
like.

BASELINE was produced by moving the two new files out of the tree and running
again -- not by argument. The two runs:

    baseline (new files absent)   117 failed, 9919 passed, 27 skipped, 1 xfailed, 23 errors
    with new files               117 failed, 9941 passed, 27 skipped, 1 xfailed, 23 errors

The sorted FAILED/ERROR lists are IDENTICAL. The only difference between the
two runs is the passed count, +22, which is exactly the new tests. Zero new
failures, zero failures fixed, zero errors introduced.

Nothing outside its own two files imports the module (`grep -rn
pmus_acquisition_rest --include=*.py`), which is why that result is the
expected one.

## The 117 are pre-existing and partly environmental

This is NOT the "22 failed / 4,243 passed" figure carried in earlier notes;
that figure was from a different subset and should not be used as the baseline
for this container. The 117 are the baseline HERE, measured today, and they
include failures caused by the container's egress policy rather than by code:
the run logged 229 denied outbound connections, `api.gemini.com:443` and
`gateway.polymarket.us:443` among them, from tests that reach for the live
venue. They are unchanged by this work and are not investigated here.

## Still true

The flag is OFF by default and the engine patch is NOT applied. A passing test
suite says the predicate behaves as specified; it says nothing about whether
holding an acquisition quote longer makes money. The two evidenced Gemini fills
were both trade-throughs and neither completed a pair, which argues the other
way, and the PMUS per-update best-bid measurement needed to fix a threshold has
not been done.
