"""The graft: P39's harness driving the canonical post-Probe-08 execution core.

These two lines diverged at c668665. The harness (runner, authenticated book)
was proven by two days of continuous DISARMED operation; the execution core
was proven by Probes 04-08's real fills. Neither had ever run against the
other, and the deployed P39 image carried an execution core that predates
every Probe 04-08 fix.

Every assertion here reads the parse tree or the import graph. None greps
prose: this module's own docstrings name the things it forbids, and a text
search trips over them.
"""
import ast
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "src", "l5_pmus_live_runner.py")
DOCKERFILE = os.path.join(ROOT, "deploy", "l5_pmus_live", "Dockerfile")
ENTRY = "src.l5_pmus_live_runner"


def _tree(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _call_named(tree, dotted):
    """Every Call whose func renders as `dotted`."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            parts = []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            if ".".join(reversed(parts)) == dotted:
                out.append(n)
    return out


def _kwargs(call):
    return {k.arg: k.value for k in call.keywords if k.arg}


# --------------------------------------------------------------- the bounds

def test_the_runner_carries_exactly_the_authorized_bounds():
    """MAX_LIVE_ORDERS MOVED FROM 8 TO 1, by owner authorization on
    2026-10-14, and it is now the only bound that is configurable.

    It was hardcoded at 8, which meant the bounded pilot's "one outstanding
    order" restriction was not enforceable at all: the $1 cash cap bounds
    DOLLARS, so at a 3c quote eight clips could rest at once and still total
    under a dollar. The dollar bound was never the problem; the count bound
    did not exist.

    Every other bound stays pinned to its literal, so this test still fails
    on any drift it was written to catch. The live-order bound is asserted
    against `max_live_orders()` rather than a literal because it is now
    deployment configuration -- and the default is pinned separately below,
    so a change to THAT does not slip through either.
    """
    from src import l5_pmus_live_runner as R
    assert {k: str(v) for k, v in R.PROBE_LIMITS.items()} == {
        "max_usd_per_market": "20",
        "max_abs_residual": "10",
        "max_total_exposure_usd": "20",
        "max_concurrent_markets": "1",
        "max_live_orders": str(R.max_live_orders()),
        "daily_realized_loss_halt_usd": "5",
    }


def test_the_authorized_live_order_default_is_one(monkeypatch):
    """The pilot default, pinned on its own. Raising it must be deliberate."""
    from src import l5_pmus_live_runner as R
    monkeypatch.delenv("L5_MAX_LIVE_ORDERS", raising=False)
    assert R.max_live_orders() == 1


def test_the_bounds_are_accepted_by_the_canonical_limits_object():
    """A limit the executor silently ignores is not a limit."""
    from src import l5_live_execution as LX
    from src import l5_pmus_live_runner as R
    lim = LX.SafetyLimits(**R.PROBE_LIMITS)
    assert lim.max_live_orders == R.max_live_orders()
    assert lim.max_live_orders >= 1       # zero would refuse the first order
    assert str(lim.max_total_exposure_usd) == "20"
    assert str(lim.max_abs_residual) == "10"
    assert str(lim.daily_realized_loss_halt_usd) == "5"


# ------------------------------------------------------- the executor wiring

def test_the_executor_is_wired_as_the_traded_baseline():
    """maker_treatment + maker_only, and NO Probe 08 treatment.

    Read off the call, not off a comment: the wiring is what the constructor
    is actually handed.
    """
    calls = _call_named(_tree(RUNNER), "LX.FaithfulExecutor")
    assert len(calls) == 1
    kw = _kwargs(calls[0])
    assert isinstance(kw["maker_treatment"], ast.Constant)
    assert kw["maker_treatment"].value is True
    assert kw["maker_only"].value is True
    assert kw["suppress_imbalance_increase"].value is False
    # the parameter that no longer exists on the canonical core
    assert "live" not in kw


def test_every_executor_kwarg_is_one_the_canonical_core_accepts():
    """The graft's real failure mode: a kwarg the old core took and the new
    one does not (`live=`, `book_source=`) raises only at construction, which
    live means at window open with money authorized."""
    import inspect
    from src import l5_live_execution as LX
    from src import l5_pmus_transport as T
    for dotted, target in (("LX.FaithfulExecutor", LX.FaithfulExecutor),
                           ("T.PmusTransport", T.PmusTransport)):
        calls = _call_named(_tree(RUNNER), dotted)
        assert calls, dotted
        accepted = set(inspect.signature(target.__init__).parameters)
        for c in calls:
            assert set(_kwargs(c)) <= accepted, (dotted, set(_kwargs(c)) - accepted)


def test_the_runner_only_calls_attributes_that_exist_on_the_core():
    """Catches T._num / LX.to_grid / t.market_meta style rot."""
    from src import l5_live_execution as LX
    from src import l5_pmus_transport as T
    tree = _tree(RUNNER)
    seen = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id in ("LX", "T")):
            seen.add((n.value.id, n.attr))
    mods = {"LX": LX, "T": T}
    missing = [f"{m}.{a}" for m, a in sorted(seen) if not hasattr(mods[m], a)]
    assert not missing, missing


def test_every_method_the_runner_calls_on_the_core_objects_exists():
    """The graft bug the kwarg check missed.

    `store.resting()`, `executor.end_resting()` and `executor.poll_resting()`
    were P39 methods the canonical core never had. Nothing raised until the
    container was running, because an attribute on `self.store` is resolved
    at call time, not at import. The runner reached the venue, failed
    closed, and logged the same AttributeError every three seconds.

    Checking constructor kwargs cannot see this; only the attribute names
    can. Receivers are matched by the name they are bound to in the runner.
    """
    import tempfile
    from src import l5_live_execution as LX
    from src import l5_pmus_transport as T
    from src import l5_pmus_live_runner as R
    # INSTANCES, not classes: `armed`, `market_slug` and `tick_size` are set
    # in __init__, so a class-level hasattr would call them missing and the
    # real rot would be lost in the noise.
    with tempfile.TemporaryDirectory() as d:
        store = LX.InventoryStore(os.path.join(d, "inv.json"))
        limits = LX.SafetyLimits(**R.PROBE_LIMITS)
        transport = T.PmusTransport(market_slug="cpc-btc-updown-15m-x")
        ex_obj = LX.FaithfulExecutor(store, limits, transport=transport)
        receivers = {
            "self.store": store,
            "self.executor": ex_obj,
            "ex": ex_obj,
            "self.transport": transport,
            "t": transport,
        }
        def rendered(node):
            parts = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
                return ".".join(reversed(parts))
            return None

        missing = []
        for n in ast.walk(_tree(RUNNER)):
            if not isinstance(n, ast.Attribute):
                continue
            base = rendered(n.value)
            if base in receivers and not hasattr(receivers[base], n.attr):
                missing.append("%s.%s" % (base, n.attr))
        assert sorted(set(missing)) == [], sorted(set(missing))


def test_the_runner_moves_venue_fills_into_inventory():
    """Without an order-progress read, residual stays 0 and every exposure
    number downstream is wrong. Assert the three canonical calls are wired."""
    tree = _tree(RUNNER)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "live_orders" in names
    assert "apply_order_progress" in names
    assert "order_progress" in names        # LX.order_progress(row)
    assert "mark_uncertain" in names        # unknowable stays unknowable


def test_no_intra_window_cancel_and_replace():
    """Re-pricing a resting order toward L5's newer price is chasing under
    another name. The traded lifecycle rests, and the deadman clears."""
    tree = _tree(RUNNER)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "end_resting" not in names
    assert "poll_resting" not in names
    assert _call_named(tree, "LX.deadman"), "window end must still deadman"


# ------------------------------------------------------------ the boundaries

def _closure(entry):
    def path_of(m):
        p = os.path.join(ROOT, m.replace(".", "/") + ".py")
        if os.path.exists(p):
            return p
        p2 = os.path.join(ROOT, m.replace(".", "/"), "__init__.py")
        return p2 if os.path.exists(p2) else None
    seen, queue, out = set(), [entry], []
    while queue:
        m = queue.pop(0)
        if m in seen:
            continue
        seen.add(m)
        p = path_of(m)
        if not p:
            continue
        out.append((m, p))
        with open(p, encoding="utf-8", errors="replace") as fh:
            try:
                t = ast.parse(fh.read())
            except SyntaxError:
                continue
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                queue += [a.name for a in n.names if a.name.startswith("src")]
            elif isinstance(n, ast.ImportFrom) and n.module \
                    and n.module.startswith("src"):
                queue.append(n.module)
                queue += ["%s.%s" % (n.module, a.name) for a in n.names]
    return out


def test_the_rejected_probe09_rule_is_unreachable_from_the_live_entrypoint():
    """Falsified by Probe 08's own stream; it must not be in the image."""
    bad = [m for m, _ in _closure(ENTRY)
           if "prospective" in m.lower() or "probe09" in m.lower()]
    assert bad == []


def test_the_image_copies_every_module_the_entrypoint_can_reach():
    """A module missing from the COPY list fails at import, in the container,
    at window open."""
    with open(DOCKERFILE, encoding="utf-8") as fh:
        copied = {ln.split()[1] for ln in fh if ln.startswith("COPY ")}
    needed = {os.path.relpath(p, ROOT) for _, p in _closure(ENTRY)}
    assert needed - copied == set(), sorted(needed - copied)


#: DATA the image needs and no import closure can see. Enumerated, so
#: adding one is a visible act rather than something that disappears into a
#: permanently-red assertion -- which is what happened: this test went red
#: on the cost-basis manifests and stayed red, and so could not catch the
#: module it exists to catch.
ALLOWED_DATA = {
    # inspected cost-basis batches; the apply aborts without them
    "config/cost_basis_batch_manifest_20261016.json",
    "config/cost_basis_batch_manifest_20261016b.json",
    "config/cost_basis_batch_manifest_20261016c.json",
    "config/cost_basis_batch_manifest_20261016d.json",
    # the frozen v2 action model the shadow controller loads at construction
    "analysis/l5/l5_action_model_v2.json",
}


#: Modules shipped DELIBERATELY although the entrypoint does not import
#: them. Same discipline as ALLOWED_DATA above: an addition here is a
#: visible, reviewed act, and the assertion stays green so it can keep
#: catching the module it exists to catch.
#:
#: Each entry needs a reason that justifies shipping an UNIMPORTED module
#: into the money-capable image.
ALLOWED_EXTRA_MODULES = {
    # config.py is imported lazily inside a function; P39 shipped it too.
    "src/polymarket_l5/config.py",
    # The effective-runtime-configuration verifier, run by hand in the
    # container as `python3 -m src.l5_runtime_config_verify`. It ships
    # because the only configuration that matters is the one the RUNNING
    # process has: checking the variables we INTENDED to set, from
    # outside, cannot see a value an accessor floors, clamps or ignores,
    # and cannot see the approved limits that are code constants with no
    # variable at all. It reads configuration and prints a verdict --
    # test_nothing_in_the_verifier_can_submit_arm_or_reach_a_venue asserts
    # it has no transport, no order path and no claim or arm call, and
    # test_the_verifier_s_own_imports_are_all_in_the_image asserts it can
    # actually run in here.
    "src/l5_runtime_config_verify.py",
}


def _copied():
    with open(DOCKERFILE, encoding="utf-8") as fh:
        return {ln.split()[1] for ln in fh if ln.startswith("COPY ")}


def test_the_image_carries_no_module_outside_that_closure():
    """Reviewability: the list is the closure, not a pile."""
    # config.py is imported lazily inside a function; P39 shipped it too.
    allowed = {os.path.relpath(p, ROOT) for _, p in _closure(ENTRY)} \
        | ALLOWED_EXTRA_MODULES
    modules = {c for c in _copied() if c.endswith(".py")}
    assert modules - allowed == set(), sorted(modules - allowed)


def test_every_deliberate_extra_module_is_actually_shipped():
    """The allowlist is not a wishlist. An entry that is NOT in the image
    means either the COPY was dropped or the entry is stale, and both should
    be noticed here rather than discovered by a container that cannot run
    the verifier the preflight depends on."""
    missing = ALLOWED_EXTRA_MODULES - _copied()
    assert missing == set(), sorted(missing)


def test_the_verifier_s_own_imports_are_all_in_the_image():
    """Shipping the verifier is pointless if it cannot import inside the
    image. Its module-level imports are stdlib; these are the ones it pulls
    in inside `verify()`, which the ast closure above cannot see."""
    shipped = _copied()
    for mod in ("src/l5_campaign_approval.py",
                "src/l5_defensive_completion.py",
                "src/l5_live_authorization.py",
                "src/l5_pmus_live_runner.py",
                "src/l5_two_sided_probe.py"):
        assert mod in shipped, mod


def test_the_image_carries_no_unaccounted_data_file():
    """The same claim for the files the ast closure cannot see. A data
    dependency that is missing fails at RUNTIME and looks like health --
    without the v2 artifact the shadow controller self-disables and the
    service emits no decisions while every gauge reads fine."""
    data = {c for c in _copied() if not c.endswith(".py")}
    assert data == ALLOWED_DATA, {"unexpected": sorted(data - ALLOWED_DATA),
                                  "missing": sorted(ALLOWED_DATA - data)}


def test_the_v2_artifact_the_shadow_controller_loads_is_in_the_image():
    """Named directly, because it is the one data file whose absence is
    silent: `load_v2` raises, the controller turns itself off, and nothing
    else in the image notices."""
    from src import pmus_l5_persistence_v2 as pv2
    rel = os.path.relpath(str(pv2.ARTIFACT), ROOT)
    assert rel in _copied(), rel


# ------------------------------------------------------------------- disarm

def test_disarm_removes_the_ability_to_post_not_just_the_flag():
    from src import l5_pmus_transport as T
    t = T.PmusTransport(market_slug="cpc-btc-updown-15m-2026-09-26-1400z")
    t.armed = True
    t.http = object()                    # stand in for the signed transport
    t._injected = False
    out = t.disarm("window_end")
    assert t.armed is False
    assert out["was_armed"] is True and out["post_capable"] is False
    with pytest.raises(Exception):
        t.http.request("POST", "/v1/orders", {}, "https://example.invalid")


def test_disarm_leaves_an_injected_transport_alone():
    from src import l5_pmus_transport as T
    sentinel = object()
    t = T.PmusTransport(market_slug="m", http=sentinel)
    t.armed = True
    t.disarm("x")
    assert t.http is sentinel and t.armed is False


def test_disarm_is_idempotent_and_never_raises():
    from src import l5_pmus_transport as T
    t = T.PmusTransport(market_slug="m")
    assert t.disarm()["was_armed"] is False
    assert t.disarm()["was_armed"] is False


# ------------------------------------------------------------ frozen L5_NATIVE

@pytest.mark.parametrize("rel", ["src/l5_faithful_translation.py",
                                 "src/l5_execution_translations.py"])
def test_l5_native_is_byte_identical_to_030d8bf(rel):
    frozen = subprocess.run(["git", "show", "030d8bf:%s" % rel], cwd=ROOT,
                            capture_output=True)
    assert frozen.returncode == 0
    with open(os.path.join(ROOT, rel), "rb") as fh:
        assert fh.read() == frozen.stdout
