"""The settlement sweep must collect economics and must not be able to trade.

OWNER 2026-09-11: "Do not restart the trading campaign just to collect
settlements. Run a read-only settlement/audit pass... No order path, no
re-arm, no new risk."

The campaign stopped itself on the daily loss stop, and the settlement auditor
only ran inside it, so 36 markets would never get a settled/realized row. This
module closes that without restarting anything that can spend money -- and the
tests below are what make "cannot spend money" a property of the code rather
than a promise in a docstring.
"""
from __future__ import annotations

import ast
import inspect

from src import pmus_settlement_sweep as sw


def _audit(slug, **kw):
    return {"slug": slug, "status": "REALIZED", "settled": True,
            "realized_confirmed": True, "realized_usd": "-1.7500",
            "breakdown": {"matched_pairs": "0", "matched_pair_cost": None,
                          "matched_contribution_usd": "0",
                          "directional_residual": "5",
                          "maker_fills": 1, "taker_fills": 0,
                          "incentives": {"credited_to_us_usd": "0.0100"}}}


def _unconfirmed(slug, **kw):
    """Resolved with the account already flat: the venue emits no
    POSITION_RESOLUTION, so there IS no realized figure. wba-saw's shape."""
    return {"slug": slug, "status": "SETTLED_REALIZED_UNCONFIRMED",
            "settled": True, "realized_confirmed": False, "realized_usd": None,
            "breakdown": {"matched_pairs": "16.08",
                          "matched_pair_cost": "0.8954",
                          "matched_contribution_usd": "1.6820",
                          "directional_residual": "0.00",
                          "maker_fills": 10, "taker_fills": 0,
                          "incentives": {"credited_to_us_usd": "0.1100"}}}


# ------------------------------------------------ cannot trade, structurally
def test_the_sweep_has_no_order_path_at_all():
    src = inspect.getsource(sw)
    for banned in ("place_limit", "submit_order", "post_only", "cancel_order",
                   "pmus_spring_l5_session", "run_campaign", "daily_loss_stop",
                   "Transport(", "os.environ"):
        assert banned not in src, banned


def test_it_imports_the_auditor_and_nothing_that_can_spend():
    tree = ast.parse(inspect.getsource(sw))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            # `from src import x` names the module in the alias, not the module
            mods.update(f"{node.module}.{a.name}" for a in node.names)
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert "src.pmus_settlement_auditor" in mods
    assert not any("executor" in m or "micro" in m for m in mods), mods


def test_it_walks_once_and_never_re_arms():
    """A sweep that reschedules itself is a campaign with another name."""
    src = inspect.getsource(sw.sweep)
    assert "while" not in src, "no loop but the single pass over the list"
    assert "never re-arms" in src or "never re-arms" in inspect.getsource(sw)


# ----------------------------------------------------------- the accounting
def test_realized_is_only_ever_the_venue_confirmed_number():
    got = sw.sweep(["a"], write_ledger=False, sleep=lambda s: None,
                   audit=_audit)
    assert got["totals"]["venue_confirmed_realized_usd"] == "-1.7500"
    assert got["totals"]["markets_realized"] == 1


def test_an_unconfirmed_market_never_reaches_the_realized_total():
    """THE RULE THE OWNER RESTATED TODAY: do not count wba-saw or 1win-nemi as
    realized until venue cashflow is confirmed. Keep derived separate."""
    got = sw.sweep(["wba-saw"], write_ledger=False, sleep=lambda s: None,
                   audit=_unconfirmed)
    t = got["totals"]
    assert t["venue_confirmed_realized_usd"] == "0"
    assert t["markets_realized"] == 0
    assert t["derived_matched_contribution_usd"] == "1.6820"
    assert t["markets_derived_only"] == 1
    assert got["markets"][0]["realized_usd"] is None


def test_the_two_totals_are_never_summed_into_one():
    got = sw.sweep(["a", "b"], write_ledger=False, sleep=lambda s: None,
                   audit=lambda s: _audit(s) if s == "a" else _unconfirmed(s))
    t = got["totals"]
    assert t["venue_confirmed_realized_usd"] == "-1.7500"
    assert t["derived_matched_contribution_usd"] == "1.6820"
    assert "NEVER" in t["labels"]
    # credited incentives accumulate across both, since a credit is a credit
    assert t["credited_incentives_usd"] == "0.1200"


def test_an_absent_realized_is_not_a_zero():
    assert sw.totals([])["venue_confirmed_realized_usd"] == "0"
    assert sw.totals([])["markets_realized"] == 0, (
        "zero MARKETS, not a market worth zero")


# -------------------------------------------------------------- robustness
def test_one_unreadable_market_does_not_cost_the_others():
    def flaky(slug):
        if slug == "bad":
            raise RuntimeError("429 from the venue")
        return _audit(slug)
    got = sw.sweep(["a", "bad", "c"], write_ledger=False,
                   sleep=lambda s: None, audit=flaky)
    assert got["n_audited"] == 2 and got["n_errors"] == 1
    assert got["errors"][0]["slug"] == "bad"
    assert "429" in got["errors"][0]["error"]


def test_the_pass_is_paced_between_markets_but_not_after_the_last():
    calls = []
    sw.sweep(["a", "b", "c"], write_ledger=False, sleep=calls.append,
             audit=_audit)
    assert calls == [sw.PACE_S, sw.PACE_S], "n-1 pauses for n markets"


def test_the_default_source_is_the_ledger_not_a_hardcoded_list():
    """The 36 are whatever the ledger says they are at run time. A pinned
    list would be stale the moment one settles."""
    src = inspect.getsource(sw.pending_slugs)
    assert "unsettled_slugs" in src
    assert "36" not in inspect.getsource(sw.sweep)


def test_the_sweep_is_packaged_in_the_probe_image():
    """It must be IN the image before it can be the start command. Shipping
    the COPY separately means the start command can be switched without a
    build that would otherwise relaunch the campaign in between."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    rail = (root / "deploy/pmus_spring_l5/railway.json").read_text()
    assert "COPY src/pmus_settlement_sweep.py" in docker
    assert "src/pmus_settlement_sweep.py" in rail


def test_the_start_command_is_the_sweep_by_explicit_owner_decision():
    """The latch did its job. It asserted the CAMPAIGN until an owner decision
    changed it; the owner chose "run it now, two-step" on 2026-09-11, so this
    now asserts the sweep.

    Why the repo config carries it and not only the Railway API: the
    update-service write cannot be read back (every read of that service's
    config returns a schema error), and an update that silently failed would
    mean the next build starts the TRADING CAMPAIGN -- the one outcome the
    owner forbade. With both the service setting and railway.json naming the
    sweep, a failed API write cannot relaunch trading.

    Restoring the campaign is likewise an owner decision and will flip this
    test back."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rail = json.loads((root / "deploy/pmus_spring_l5/railway.json").read_text())
    assert rail["deploy"]["startCommand"] == "python3 -m src.pmus_settlement_sweep"
    assert rail["deploy"]["restartPolicyType"] == "NEVER", (
        "the sweep exits when done; ALWAYS would re-run it forever")


# ------------------------------- a zero must carry its own evidence (LIVE)
def test_the_sweep_reports_where_it_read_the_ledger_from():
    """LIVE 17:05:02Z: the first sweep returned n=0 in 1.5 seconds and the row
    could not distinguish "nothing is unsettled" from "the ledger was not
    found". It meant the first, but establishing that took the Dockerfile's
    ENV, the volume mount line and an hour-old risk_set row. A zero that
    cannot be read as a measurement is not one."""
    import contextlib, io, json
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = sw.sweep([], write_ledger=False, sleep=lambda s: None,
                       audit=_audit)
    start = json.loads([l for l in buf.getvalue().splitlines()
                        if '"sweep_start"' in l][0].split(" ", 1)[1])
    for field in ("state_dir", "ledger", "ledger_exists", "rows",
                  "filled_markets", "settled_markets"):
        assert field in start["ledger"], field
    assert "ledger" in got


def test_a_census_failure_does_not_stop_the_sweep():
    import contextlib, io, json
    orig = sw.ledger_census
    sw.ledger_census = lambda: (_ for _ in ()).throw(OSError("no mount"))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sw.sweep(["a"], write_ledger=False, sleep=lambda s: None,
                     audit=_audit)
        start = json.loads([l for l in buf.getvalue().splitlines()
                            if '"sweep_start"' in l][0].split(" ", 1)[1])
        assert "census_error" in start["ledger"]
    finally:
        sw.ledger_census = orig
