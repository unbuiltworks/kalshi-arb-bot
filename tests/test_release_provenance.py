"""A RELEASE that names the wrong code is worse than one that names none.

The supervisor stamps every segment with the commit it believes it is running,
read from deploy/l5_exec_shadow/RELEASE because the image ships no .git. At
21:46Z on 2026-09-17 that field still read b7a2e86e while the image served the
gemini per-side counters, so a segment's provenance pointed at a commit whose
shadow did not have them. The field is only worth having if it is right, so a
promotion that forgets to refresh it fails here rather than in the record.
"""
import hashlib
import json
import os

import pytest

from src.l5_research_supervisor import STREAMS

RELEASE = "deploy/l5_exec_shadow/RELEASE"


def _release():
    with open(RELEASE, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_release_records_every_stream_it_serves():
    mods = _release()["served_modules"]["modules"]
    assert set(mods) == set(STREAMS), (
        "a stream was added or removed without refreshing RELEASE")


def test_every_recorded_module_sha_matches_the_file_on_disk():
    """This is the check that would have caught the stale code_sha: the
    gemini shadow's bytes changed, so its recorded SHA would have stopped
    matching and the promotion could not have shipped silently."""
    stale = []
    for name, rec in _release()["served_modules"]["modules"].items():
        path = os.path.join(*rec["module"].split(".")) + ".py"
        assert os.path.exists(path), "%s names a module that is gone" % name
        with open(path, "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        if got != rec["module_sha256"]:
            stale.append("%s: RELEASE says %s, disk is %s"
                         % (name, rec["module_sha256"][:12], got[:12]))
    assert not stale, (
        "RELEASE is stale -- refresh deploy/l5_exec_shadow/RELEASE before "
        "promoting, or segments will be attributed to code that did not "
        "produce them:\n  " + "\n  ".join(stale))


def test_the_recorded_module_names_match_the_supervisor_not_a_copy():
    """Recording a module name by hand lets it drift from what actually runs."""
    mods = _release()["served_modules"]["modules"]
    for name, spec in STREAMS.items():
        assert mods[name]["module"] == spec[0]


def test_the_code_sha_is_a_full_commit_id():
    sha = _release()["code_sha"]
    assert isinstance(sha, str) and len(sha) == 40, (
        "code_sha should be a full commit id, got %r" % (sha,))
    assert all(c in "0123456789abcdef" for c in sha)


def test_the_frozen_spec_hashes_in_release_match_the_artifacts():
    """The specs downstream evidence is labelled with. A RELEASE naming a
    hash no artifact has would attribute evidence to a spec nobody froze."""
    from src.l5_surface import params as PR
    sets = PR.load_frozen()
    frozen = _release()["frozen_specs_20260917"]
    for label, sha in frozen.items():
        if label not in sets:
            continue              # non-spec keys live here too
        assert sets[label].spec_sha256() == sha, label
