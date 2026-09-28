"""The midnight sort fix was REPORTING-ONLY. This proves it, end to end.

On 2026-09-28 `scripts/pmus_window_ledger.py` was found sorting per-window rows by
the 5-character window label, so "0030z" < "1830z" put 2026-09-28 00:00-01:15 ahead
of 2026-09-27 18:30-23:45 and every rolling n=5/10/15/20 checkpoint was built from
the first k windows LEXICOGRAPHICALLY. The fix sorts by the full slug.

The claim made when that landed was that FULL-SAMPLE scoring is invariant under row
order and only the rolling subsets moved. That claim is load-bearing -- it is the
reason the fix counts as reporting-only and did not disturb the frozen checkpoint --
so it is proven here rather than asserted: the ledger is regenerated with USABLE
iterated in REVERSED order and every full-sample section must come back identical.

Run against a temporary output path; the committed analysis files are never touched.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import pmus_window_ledger as LED                   # noqa: E402
from scripts import audit_economic_complete as AUD              # noqa: E402

#: the sections whose values must not depend on row order at all
FULL_SAMPLE = ("n_usable", "residual_statistics", "p0_thresholds", "policy",
               "capacity", "accounting", "excluded")


def _generate(tmpdir, usable, monkeypatch):
    monkeypatch.setattr(LED, "OUT", os.path.join(str(tmpdir), "led.json"))
    monkeypatch.setattr(LED, "USABLE", usable)
    monkeypatch.setattr(AUD, "USABLE", usable)
    assert LED.main([]) == 0
    out = {}
    for clip in (25, 50):
        p = LED.OUT.replace(".json", "_q%d.json" % clip)
        with open(p, encoding="utf-8") as fh:
            out[clip] = json.load(fh)
    return out


@pytest.fixture(scope="module")
def _pair(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    fwd = dict(AUD.USABLE)
    rev = dict(reversed(list(AUD.USABLE.items())))
    assert list(fwd) != list(rev), "need at least two windows to permute"
    a = _generate(tmp_path_factory.mktemp("fwd"), fwd, mp)
    b = _generate(tmp_path_factory.mktemp("rev"), rev, mp)
    mp.undo()
    return a, b


@pytest.mark.parametrize("clip", [25, 50])
@pytest.mark.parametrize("section", FULL_SAMPLE)
def test_every_full_sample_section_is_order_invariant(_pair, clip, section):
    a, b = _pair
    assert a[clip][section] == b[clip][section], section


@pytest.mark.parametrize("clip", [25, 50])
def test_the_per_window_rows_are_the_same_set_in_the_same_order(_pair, clip):
    """The fix sorts, so even a reversed input must emit one canonical order."""
    a, b = _pair
    assert [r["slug"] for r in a[clip]["per_window"]] == \
           [r["slug"] for r in b[clip]["per_window"]]
    assert a[clip]["per_window"] == b[clip]["per_window"]


@pytest.mark.parametrize("clip", [25, 50])
def test_the_canonical_order_is_chronological_not_lexicographic(_pair, clip):
    """The defect itself, pinned: sorting by the 5-char label would put the
    post-midnight windows first. Sorting by slug must not."""
    a, _ = _pair
    slugs = [r["slug"] for r in a[clip]["per_window"]]
    assert slugs == sorted(slugs), "rows must be in full-slug order"
    labels = [r["window"] for r in a[clip]["per_window"]]
    if any(l < "1000z" for l in labels) and any(l > "1700z" for l in labels):
        assert labels != sorted(labels), (
            "this sample spans midnight, so the chronological order MUST differ "
            "from the lexicographic one -- if it does not, the defect could not "
            "have been detected and this test proves nothing")


@pytest.mark.parametrize("clip", [25, 50])
def test_the_rolling_checkpoints_are_also_stable_once_sorted(_pair, clip):
    a, b = _pair
    assert a[clip]["rolling"] == b[clip]["rolling"]
