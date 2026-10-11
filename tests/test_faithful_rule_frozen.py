"""The faithful translation rule is FROZEN at 030d8bf.

The five-window development sample is closed. Everything after it is
prospective out-of-sample validation, and that only means anything if the
rule being validated is the rule that was specified BEFORE the new windows
were seen. These tests are the tripwire: they fail loudly if the frozen
modules change, rather than letting a silent edit turn out-of-sample
validation back into in-sample fitting.

If a change is genuinely intended, the hashes below must be updated in the
SAME commit that changes the behaviour, with the reason stated -- so the
freeze is broken visibly and on the record, never by accident.
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

from src import l5_faithful_translation as F

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: sha256 prefixes at the freeze commit 030d8bf
FROZEN = {
    "src/l5_faithful_translation.py": "e2f786e00c800dbf",
    "src/l5_fidelity_report.py": "c10f7884bb328dc7",
    "src/l5_execution_translations.py": "b043893ba983ec56",
}

#: the closed development sample. These five windows produced the rule and
#: can therefore never count as out-of-sample evidence for it.
DEV_SAMPLE = ("2026-09-23T16:15", "2026-09-23T16:30", "2026-09-23T16:45",
              "2026-09-23T17:30", "2026-09-23T19:30")


@pytest.mark.parametrize("rel,want", sorted(FROZEN.items()))
def test_the_frozen_modules_are_unchanged(rel, want):
    got = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:16]
    assert got == want, (
        "%s changed. The faithful rule is frozen at 030d8bf for prospective "
        "validation; if this change is intended, update the hash in this "
        "commit and state why." % rel)


def test_the_rule_still_has_no_tunable_threshold():
    # The rule's only constants are inherited, not chosen: the staleness
    # bound comes from the collector's own 2.0s snapshot TTL. A new numeric
    # knob appearing here would mean the rule had become tunable.
    assert F.STALE_MAX_S == 2.0
    assert F.MAKER == "MAKER" and F.TAKER == "TAKER"
    assert F.LAYERS == ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY")


def test_the_safety_overlay_defaults_are_explicit_and_separate():
    from decimal import Decimal as D
    lim = F.SafetyLimits()
    assert lim.enabled is True
    assert lim.band == (D("0.20"), D("0.80"))
    assert lim.max_committed_usd == D("25")
    assert lim.max_abs_residual == D("25")
    # and the venue can always be measured without them
    assert F.SafetyLimits(enabled=False).refuse(D("0.99"), D(1), D(0), D(0)) is None
