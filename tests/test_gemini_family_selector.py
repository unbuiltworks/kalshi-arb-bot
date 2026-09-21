"""--family and --envelope, through the ACTUAL runner path.

WHY THIS FILE EXISTS. `eligible_symbol(cohort=...)` and
contract_cohort.BTC15M both already existed, and NOTHING threaded them, so
every invocation fell through to CC.DEFAULT (BTC5m). A BTC15m envelope could
have been asked for and a BTC05M contract entered -- the authorization saying
one thing and the wire doing another, which is the exact failure the q-size
work already cost a live grant to find. I proposed a BTC15m envelope without
checking the runner could select BTC15m.

So these tests drive `main()` and `_build()`, not the helpers underneath them.
A test that only proved `eligible_symbol(cohort=CC.BTC15M)` works would have
passed for the entire period the defect existed.
"""
import asyncio
from decimal import Decimal as D
from unittest import mock

import pytest

from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC


# ------------------------------------------------------------- the mapping

def _parsed(argv):
    """What main() resolves BEFORE it runs anything. Captured from the real
    parser and the real dict, by intercepting main_async."""
    seen = {}

    async def _fake(auth_id, **kw):
        seen.update(kw)
        seen["auth_id"] = auth_id
        return {"state": "CAPTURED"}

    with mock.patch.object(RC, "main_async", _fake):
        RC.main(argv)
    return seen


BASE = ["--auth-id", "A", "--out", "/dev/null", "--wire", "/dev/null"]


def test_family_btc15m_with_its_envelope_resolves_exactly():
    got = _parsed(BASE + ["--family", "btc15m",
                          "--envelope", "live_q1_btc15m"])
    assert got["family"] is CC.BTC15M
    assert got["envelope"] is LE.LIVE_Q1_BTC15M
    assert got["envelope"].name == "GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1"
    assert got["envelope"].action_size == D("1")
    assert got["envelope"].cohort_prefix == "GEMI-BTC15M"


def test_omitting_family_still_resolves_to_btc5m():
    """Existing invocations must not silently change family."""
    got = _parsed(BASE)
    assert got["family"] is CC.BTC5M
    assert got["family"].prefix == "BTC05M"
    assert got["envelope"] is LE.SHADOW_Q001      # and the default envelope


def test_the_btc15m_envelope_cannot_silently_fall_back_to_live_q1():
    got = _parsed(BASE + ["--family", "btc15m",
                          "--envelope", "live_q1_btc15m"])
    assert got["envelope"] is not LE.LIVE_Q1
    assert got["envelope"] is not LE.SHADOW_Q001
    # and live_q1 still means live_q1
    assert _parsed(BASE + ["--envelope", "live_q1"])["envelope"] is LE.LIVE_Q1


def test_an_unknown_family_or_envelope_is_refused_by_the_parser():
    for bad in (["--family", "btc30m"], ["--envelope", "live_q10"],
                ["--envelope", "LIVE_Q1_BTC15M"]):
        with pytest.raises(SystemExit):
            _parsed(BASE + bad)


# --------------------------------------------- symbol eligibility per family

def test_family_btc15m_selects_only_btc15m_symbols():
    sel = RC.eligible_symbol(cohort=CC.BTC15M)
    for row in ([sel.get("chosen")] if sel.get("chosen") else []) + list(
            sel.get("considered") or []):
        assert str(row["symbol"]).startswith("GEMI-BTC15M"), row


def test_family_btc5m_selects_only_btc05m_symbols():
    sel = RC.eligible_symbol(cohort=CC.BTC5M)
    for row in ([sel.get("chosen")] if sel.get("chosen") else []) + list(
            sel.get("considered") or []):
        assert str(row["symbol"]).startswith("GEMI-BTC05M"), row


def test_the_two_families_never_produce_each_others_symbols():
    a = RC.eligible_symbol(cohort=CC.BTC5M)
    b = RC.eligible_symbol(cohort=CC.BTC15M)
    sa = {r["symbol"] for r in (a.get("considered") or [])}
    sb = {r["symbol"] for r in (b.get("considered") or [])}
    assert not (sa & sb)
    assert all("BTC05M" in s for s in sa)
    assert all("BTC15M" in s for s in sb)


# ------------------------------- incompatible pairs fail closed, in _build

def _build_once(**kw):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        RC._build("AUTH-TEST", state_dir="/tmp/x",
                  ledger_path="/dev/null", **kw))


@pytest.mark.parametrize("family,envelope", [
    (CC.BTC5M, LE.LIVE_Q1_BTC15M),      # 15m bounds on the 5m family
    (CC.BTC15M, LE.LIVE_Q1),            # 5m bounds on the 15m family
])
def test_an_incompatible_family_envelope_pair_refuses_before_any_order(
        family, envelope):
    """A bound derived for one market does not transfer to another, and the
    refusal must land BEFORE the latch so no authorization is spent."""
    rep = _build_once(family=family, envelope=envelope)
    assert rep["state"] == WC.DO_NOT_TRADE
    assert "is sized for" in rep["why"]
    assert rep["latch"]["consumed"] is False
    assert rep.get("symbol_selection") is None, (
        "the refusal must precede symbol selection, not follow it")


def test_the_matching_pairs_are_not_refused_for_family_reasons():
    for family, envelope in ((CC.BTC5M, LE.LIVE_Q1),
                             (CC.BTC15M, LE.LIVE_Q1_BTC15M)):
        rep = _build_once(family=family, envelope=envelope)
        assert "is sized for" not in (rep.get("why") or ""), (family, envelope)


def test_the_shadow_envelope_is_family_agnostic():
    """SHADOW_Q001 carries the 5m prefix, so it is checked like any other."""
    rep = _build_once(family=CC.BTC5M, envelope=LE.SHADOW_Q001)
    assert "is sized for" not in (rep.get("why") or "")


# ------------------------------------ the evidence record names both

def test_the_report_records_the_RESOLVED_family_and_envelope():
    """So what reached the wire can be verified after the fact, rather than
    inferred from the command line someone believes was used."""
    rep = _build_once(family=CC.BTC15M, envelope=LE.LIVE_Q1_BTC15M)
    assert rep["family"]["name"] == "BTC15m"
    assert rep["family"]["prefix"] == "BTC15M"
    assert rep["family"]["resolved_symbol_prefix"] == "GEMI-BTC15M"
    assert rep["family"]["explicit"] is True
    assert rep["envelope_family"] == "GEMI-BTC15M"
    assert rep["envelope_name"] == "GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1"
    assert rep["envelope"]["action_size"] == "1"


def test_an_implicit_family_is_recorded_as_implicit():
    rep = _build_once(envelope=LE.SHADOW_Q001)
    assert rep["family"]["name"] == "BTC5m"
    assert rep["family"]["explicit"] is False


def test_the_printed_evidence_line_carries_both():
    """The stdout line is what a job log preserves when the artifact is lost."""
    import inspect
    src = inspect.getsource(RC.main)
    assert '"family"' in src and '"envelope_name"' in src
    assert '"envelope_family"' in src


# --------------------------------------------------- nothing else moved

def test_ws_continuous_and_the_bounds_were_not_touched():
    """THE BOUNDS, NOT A SNAPSHOT OF THE FILE.

    This pinned a literal executable_sha, which made it a MOMENT rather than
    an invariant: it had to break the first time ws_continuous.py changed for
    a reason nobody objects to, and it did -- on the authorized removal of the
    BTC5m hard-coding from Driver.__init__. The third time this shape has
    bitten (see the bundle test and the cohort-consumed test). What must hold
    is that the DOLLAR AND ORDER BOUNDS did not move, and that the hash names
    the file actually running.
    """
    assert WC.AUTHORIZED_SHA is None or WC.AUTHORIZED_SHA == WC.executable_sha()
    assert len(WC.executable_sha()) == 64
    assert LE.LIVE_Q1_BTC15M.max_working_orders == 2
    assert LE.LIVE_Q1_BTC15M.max_committed_usd == D("2.50")
    assert LE.LIVE_Q1_BTC15M.max_residual_usd == D("2.00")
    assert LE.LIVE_Q1.max_working_orders == 2


def test_the_tree_is_unarmed():
    """THE TREE IS ARMED, and this asserts the property that survives it.

    ARMED 2026-09-21 under OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1
    for ONE BTC15m q=1 window on cohort COHORT-2026-09-21-BTC15M-Q1LIVE-1.

    "every constant is None" was the shape of a DISARMED tree, not an
    invariant: asserting it means no authorization may ever be granted, and
    a test that must be deleted to do authorized work protects nothing. What
    holds in BOTH states is that an installed authorization names the code
    ACTUALLY RUNNING -- so any edit to the driver, the bounds or the bundle
    after the grant disarms it automatically, without anyone remembering to.

    The dual key's live half is an environment variable that is not in this
    repository, so a checkout alone still cannot mutate regardless.
    """
    from src.gemini_execution import arming, bundle
    if WC.REQUIRED_AUTH_ID is None:
        assert WC.AUTHORIZED_SHA is None
        assert WC.AUTHORIZED_ENVELOPE is None
        assert bundle.AUTHORIZED_BUNDLE is None
        assert arming.EXPECTED_AUTH_ID == "NO-AUTHORIZATION-IN-FORCE"
    else:
        assert WC.REQUIRED_AUTH_ID == arming.EXPECTED_AUTH_ID, (
            "the driver and the transport must name ONE authorization; two "
            "names is a way to spend one grant under the label of another")
        assert WC.AUTHORIZED_SHA == WC.executable_sha()
        assert WC.AUTHORIZED_ENVELOPE == WC.envelope_sha()
        assert bundle.AUTHORIZED_BUNDLE == bundle.bundle_sha()
