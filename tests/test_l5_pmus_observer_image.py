"""The observer image: complete closure, baked role, and no order path.

This service exists because the interactive session container is suspended
between turns (TD-3), so the observer could never accumulate a realtime
window across one. Moving it to Railway is a CONTINUITY change, and these
tests are what make that claim checkable rather than asserted:

  * the image's COPY set is exactly the entrypoint's import closure, so the
    worker cannot die at boot on a missing module -- the failure mode that
    silently costs BTC15m windows, which is the thing being fixed;
  * the role is baked and asserted, so a lost Dockerfile pin cross-building
    this service crashes loudly instead of writing another worker's evidence
    into this volume;
  * nothing with an order path is copied in, and the frozen experiment
    constants are unchanged by the move.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/l5_pmus_observer/Dockerfile"
ENTRY = "src.l5_pmus_realtime_observer"


def _copies() -> list[str]:
    text = DOCKERFILE.read_text()
    return [dst for _src, dst in re.findall(r"^COPY (\S+) (\S+)$", text, re.M)]


def _module_path(mod: str) -> pathlib.Path | None:
    p = ROOT / (mod.replace(".", "/") + ".py")
    if p.exists():
        return p
    p = ROOT / (mod.replace(".", "/") + "/__init__.py")
    return p if p.exists() else None


def _closure(entry: str) -> set[str]:
    """Every first-party module reachable from the entrypoint, absolute and
    relative imports alike. A walker that only follows `src.` misses
    `from . import config`, which is exactly how an image loses a module."""
    seen: set[str] = set()
    queue = [entry]
    while queue:
        mod = queue.pop()
        if mod in seen:
            continue
        path = _module_path(mod)
        if path is None:
            continue
        seen.add(mod)
        pkg = mod if (ROOT / mod.replace(".", "/") / "__init__.py").exists() \
            else mod.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "src":
                        queue.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:                       # from . / from .. import
                    base = pkg
                    for _ in range(node.level - 1):
                        base = base.rsplit(".", 1)[0]
                    target = base + ("." + node.module if node.module else "")
                else:
                    target = node.module or ""
                    if target.split(".")[0] != "src":
                        continue
                queue.append(target)
                for alias in node.names:
                    queue.append(target + "." + alias.name)
    return seen


def test_the_image_copies_every_module_the_entrypoint_imports():
    copied = set(_copies())
    missing = sorted(
        str(_module_path(m).relative_to(ROOT))
        for m in _closure(ENTRY)
        if _module_path(m) is not None
        and str(_module_path(m).relative_to(ROOT)) not in copied
    )
    assert not missing, (
        "the image would crash at boot on these modules: %s" % missing)


def test_the_model_artifact_persistence_v2_resolves_is_in_the_image():
    # persistence-v2 resolves it from the repo root, cwd-independent, so a
    # missing artifact is not caught by the module closure above.
    src = (ROOT / "src/pmus_l5_persistence_v2.py").read_text()
    assert "l5_action_model_v2.json" in src
    assert "analysis/l5/l5_action_model_v2.json" in _copies()


def test_the_role_is_baked_and_the_worker_asserts_it():
    text = DOCKERFILE.read_text()
    assert "PMUS_SERVICE_ROLE=l5-pmus-observer" in text
    from src import l5_pmus_realtime_observer as obs
    assert obs.SERVICE_ROLE == "l5-pmus-observer"

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv(obs.ROLE_ENV, "ws-recorder")
        with pytest.raises(SystemExit) as caught:
            obs.assert_role()
        assert caught.value.code == 2
        # unset stays allowed: that is how it runs locally and under test
        monkey.delenv(obs.ROLE_ENV)
        obs.assert_role()
    finally:
        monkey.undo()


def test_the_evidence_root_is_the_volume_and_is_overridable():
    text = DOCKERFILE.read_text()
    assert "L5_PMUS_OBS_ROOT=/state/l5-pmus-obs" in text
    from src import l5_pmus_realtime_observer as obs
    assert obs.STATE_DIR_ENV == "L5_PMUS_OBS_ROOT"


def test_nothing_with_an_order_path_is_copied_into_the_image():
    # The whole safety claim of this service is that it CANNOT trade. Copying
    # an executor or a venue client in would break that silently.
    banned = ("executor", "order", "session", "probe", "arm", "client",
              "signer", "wallet", "credential")
    for dst in _copies():
        name = pathlib.Path(dst).name.lower()
        assert not any(b in name for b in banned), (
            "%s looks like it could carry an order path" % dst)


def test_no_third_party_package_is_installed():
    # stdlib urllib/ssl only: no venue SDK can arrive as a dependency.
    assert "pip install" not in DOCKERFILE.read_text()


def test_the_move_does_not_change_the_frozen_experiment():
    from decimal import Decimal as D
    from src import l5_pmus_realtime_observer as obs
    from src import pmus_interaction_classifier as clf

    assert (obs.BAND_LO, obs.BAND_HI) == (D("0.20"), D("0.80"))
    assert obs.L5_SERIES == "btc-updown-15m"
    assert obs.PMUS_SERIES == "btc-updown-15m"
    assert obs.WINDOW_S == 900
    # the five frozen interaction classes, unchanged by the move, and the
    # subset that authenticated tape is required to reach
    assert clf.CLASSES == ("NO_INTERACTION", "TRADE_AT_PRICE",
                           "TRADE_THROUGH_PRICE", "QUEUE_AMBIGUOUS",
                           "OBSERVABLE_FILL_PLAUSIBLE")
    assert clf.NEEDS_TRADE_TAPE == ("TRADE_AT_PRICE", "TRADE_THROUGH_PRICE",
                                    "OBSERVABLE_FILL_PLAUSIBLE")


def test_restart_safety_is_what_makes_the_always_policy_sound():
    # Railway restarts this worker on exit; that is only safe because the tape
    # is append-only and the dedupe index is rebuilt from it at boot.
    cfg = (ROOT / "deploy/l5_pmus_observer/railway.json").read_text()
    assert '"restartPolicyType": "ALWAYS"' in cfg
    src = (ROOT / "src/l5_pmus_realtime_observer.py").read_text()
    assert "_hydrate" in src
    assert 'open(self.tape, "a")' in src
