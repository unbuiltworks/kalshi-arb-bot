"""Derive the v0.2 FIDELITY artifact from the frozen v0.1 artifact.

WHY DERIVE RATHER THAN REFIT. v0.2 changes only how the model is REPRESENTED
and IDENTIFIED, never what it learned:

  * size is emitted as a probability over preregistered bins instead of a
    median clip, and the bins are computed from the v0.1 size tables that are
    already frozen here;
  * the version string becomes a content hash of the whole model.

The gap, side and size distributions are carried across BYTE-IDENTICAL, so
v0.2 is provably the same fitted model in a new representation. Refitting from
the raw tape would risk a silent change to the distributions themselves, which
is the exact failure the content hash exists to catch.

v0.1 is preserved alongside as BTC15m.v0.1.json.gz so every v0.1 row that the
runner already froze stays auditable against the artifact that produced it.

Run:  python3 analysis/l5/fidelity/build_v0_2.py
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from src.l5_fidelity.model import size_hist, version_of  # noqa: E402

ART = os.path.join("artifacts", "l5_fidelity")
LIVE = os.path.join(ART, "BTC15m.json.gz")
ARCHIVE = os.path.join(ART, "BTC15m.v0.1.json.gz")


def main() -> int:
    with gzip.open(LIVE, "rt") as fh:
        d = json.load(fh)
    if d["version"].startswith("v0.2-"):
        print(f"already v0.2 ({d['version']}); nothing to do")
        return 0
    if not d["version"].startswith("v0.1-"):
        print(f"REFUSING: expected a v0.1 artifact, found {d['version']}")
        return 1

    if not os.path.exists(ARCHIVE):
        shutil.copy2(LIVE, ARCHIVE)
        print(f"archived {d['version']} -> {ARCHIVE}")

    t = d["tables"]
    before = {k: json.dumps(t[k], sort_keys=True)
              for k in ("gaps", "side", "size", "base_gaps", "base_side",
                        "base_size")}

    t["size_bins"] = {k: size_hist(v) for k, v in t["size"].items()}
    t["base_size_bins"] = size_hist(t["base_size"])

    # the carried-over distributions must be untouched, not merely intended to
    # be: assert it rather than claim it.
    for k, b in before.items():
        assert json.dumps(t[k], sort_keys=True) == b, f"{k} CHANGED"

    version = version_of(d["cohort"], t)
    with gzip.open(LIVE, "wt") as fh:
        json.dump({"cohort": d["cohort"], "version": version, "tables": t}, fh)
    print(f"wrote {LIVE}  {d['version']} -> {version}")
    print(f"  states with size_bins : {len(t['size_bins'])}")
    print(f"  base_size_bins        : "
          + ", ".join(f"{k} {v:.3f}" for k, v in t["base_size_bins"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
