"""The watchdog image must ship every src/ module the sweep imports.

WHY THIS FILE EXISTS. `l5_watchdog.managed_lane_ids` imports
`src.l5_managed_lanes` LAZILY, inside the function. A missing COPY in the
Dockerfile is therefore invisible at boot and at import: the watchdog
starts, sweeps, reports healthily, and raises the first time it probes a
managed lane -- which is the moment it is deciding whether a resting
order is abandoned. `l5_managed_lanes`' own docstring records the same
shape being caught in the BTC5m image on 2026-11-14 by its image-deps
test, "which is the only reason it was not shipped broken". The watchdog
had no equivalent guard.

This reads the Dockerfile as text rather than building the image, so it
runs in a plain unit-test pass.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "deploy/l5_halt_watchdog/Dockerfile"

#: modules the sweep reaches for at runtime, lazily or otherwise, that
#: live in src/ and must therefore be in the image.
REQUIRED = ("src/l5_watchdog.py", "src/l5_halt_gate.py",
            "src/l5_managed_lanes.py")


def _copied() -> set:
    text = DOCKERFILE.read_text()
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()
        if len(parts) >= 3:
            out.add(parts[1])
    return out


def test_the_dockerfile_ships_every_module_the_sweep_needs():
    missing = [m for m in REQUIRED if m not in _copied()]
    assert not missing, "not COPYed into the watchdog image: %s" % missing


def test_the_shared_lane_module_is_shipped_beside_the_watchdog():
    """The specific one that would fail only at lane-probe time."""
    assert "src/l5_managed_lanes.py" in _copied()


def test_the_lazy_import_in_managed_lane_ids_is_covered():
    """If the import moves or is renamed, this test must be updated too."""
    src = (ROOT / "src/l5_watchdog.py").read_text()
    imported = set(re.findall(r"from src import (\w+)", src))
    copied = {p.split("/")[-1][:-3] for p in _copied()
              if p.startswith("src/") and p.endswith(".py")}
    missing = sorted(m for m in imported if m not in copied)
    assert not missing, (
        "src/l5_watchdog.py imports %s, which the watchdog image does not "
        "COPY; a lazy import makes this crash at use, not at boot" % missing)


def test_the_shared_module_has_no_src_dependencies():
    """Why it is safe for every image to carry it.

    If this ever fails, the module has grown a dependency and the image
    needs that one too -- which is exactly the regress this guards.
    """
    src = (ROOT / "src/l5_managed_lanes.py").read_text()
    assert "from src import" not in src
    assert "import src." not in src
