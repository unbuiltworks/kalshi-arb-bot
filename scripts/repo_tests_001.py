"""REPO_TESTS_001 -- the two sets, and a path proof for every exclusion.

Rung-A certification requires ZERO failures/errors in the ARTIFACT-BEHAVIOUR set.
A broad-set failure may be excluded ONLY with a named, mechanically demonstrated
unreachable-behaviour proof, which this tool prints.

    BROAD              every test transitively touching ANY packaged module,
                       package markers included.
    ARTIFACT_BEHAVIOUR every test reaching a packaged module that actually
                       carries behaviour. This is the certification set.

The gap between them is not a rounding error. `src/__init__.py` is 0 bytes and
must be in the image, so under the broad relation every test that says
`from src import anything` "reaches the artifact" -- which is shared package
ancestry, not behaviour. Calling such a test disjoint would be wrong, and calling
it a blocker would be theatre; it is TEST_GRAPH_OVERINCLUSION, with the proof
printed so the claim can be checked rather than believed.

Closure logic is imported, never reimplemented -- see src/l5_import_closure.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_import_closure as C            # noqa: E402

ENTRY = "scripts/l5_pmus_live_oneshot.py"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    packaged = C.closure(ENTRY)
    bearing = C.behaviour_modules(packaged)
    markers = sorted(f for f in packaged if not C.behaviour_bearing(f))

    broad, behaviour, overinc = [], [], []
    for name in sorted(os.listdir("tests")):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        tf = os.path.join("tests", name)
        verdict, proof = C.classify(tf, packaged)
        if verdict == "DISJOINT":
            continue
        broad.append(tf)
        (behaviour if verdict == "ARTIFACT_BEHAVIOUR" else overinc).append(
            (tf, proof))

    out = {
        "entry": ENTRY,
        "packaged_files": len(packaged),
        "behaviour_bearing_modules": len(bearing),
        "package_markers_excluded_from_relevance": markers,
        "broad_set": len(broad),
        "artifact_behaviour_set": len(behaviour),
        "overinclusion_set": len(overinc),
        "certification_rule": ("zero failures/errors in artifact_behaviour_set; "
                               "a broad-set failure needs a printed path proof"),
    }
    if "--json" in argv:
        out["artifact_behaviour"] = [t for t, _ in behaviour]
        out["overinclusion"] = {t: p for t, p in overinc}
    print(json.dumps(out, indent=2))
    if "--list-behaviour" in argv:
        for t, _ in behaviour:
            print(t)
    if "--proof" in argv:
        want = argv[argv.index("--proof") + 1]
        v, p = C.classify(want, packaged)
        print("%s  %s" % (v, " -> ".join(p) if p else "(no path)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
