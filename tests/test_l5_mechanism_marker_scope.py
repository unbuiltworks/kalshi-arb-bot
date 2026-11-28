"""The one-shot marker, scoped to (experiment, market).

WHY IT WAS RESCOPED. `already_ran` returned True for a marker of ANY kind in
a single unscoped slot, so the 2026-10-18 2315z attempt spent the mechanism
PERMANENTLY on this ledger and no later authorized window could ever run.

WHAT MUST STILL HOLD, and is asserted here: same (experiment, market) refuses
a replay; a restart refuses; a pre-v2 marker keeps failing ITS OWN market
closed even though its experiment is unknown; unreadable or unattributable
state fails EVERYTHING closed; and a different market is unspent WITHOUT
being authorized -- the arm token, the market binding and the atomic claim
still decide that.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if False else "/home/user/kalshi-arb-bot")

from src import l5_cancel_mechanism as M            # noqa: E402

EXP = "sub1.MECH-CANCEL-20261019"
OLD_MKT = "cpc-btc-updown-15m-2026-10-18-2315z"
NEW_MKT = "cpc-btc-updown-15m-2026-10-19-0300z"


class Store:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.saves = 0

    def save(self):
        self.saves += 1


class Mech:
    """Only what the marker methods touch."""
    marker_identity = M.Mechanism.marker_identity
    markers = M.Mechanism.markers
    marker = M.Mechanism.marker
    already_ran = M.Mechanism.already_ran
    write_marker = M.Mechanism.write_marker

    def __init__(self, store, market):
        self.store = store
        self.market = market


def mech(store, market=NEW_MKT):
    return Mech(store, market)


@pytest.fixture(autouse=True)
def _exp(monkeypatch):
    monkeypatch.setenv("L5_EXPERIMENT_ID", EXP)


# ------------------------------------------------------------ identity
def test_the_identity_is_experiment_and_market():
    assert mech(Store()).marker_identity() == "%s|%s" % (EXP, NEW_MKT)


def test_a_missing_experiment_is_unknown_not_blank(monkeypatch):
    monkeypatch.delenv("L5_EXPERIMENT_ID", raising=False)
    assert mech(Store()).marker_identity() == "?|%s" % NEW_MKT


# ------------------------------------------- same market replay refusal
def test_a_fresh_ledger_is_unspent():
    assert mech(Store()).already_ran() is False


def test_the_same_experiment_and_market_refuses_a_replay():
    st = Store()
    m = mech(st)
    assert m.already_ran() is False
    m.write_marker({"t": "now", "coid": "c1", "stage": "SUBMITTING"})
    assert mech(st).already_ran() is True, "a replay must refuse"


def test_a_process_restart_still_refuses_the_same_pair():
    """The marker is durable state, so a NEW object over the SAME store is
    exactly what a restart looks like."""
    st = Store()
    mech(st).write_marker({"t": "now", "coid": "c1"})
    for _ in range(3):
        assert mech(st).already_ran() is True


# ------------------------------------------------- cross-market isolation
def test_a_different_market_is_unspent():
    st = Store()
    mech(st, OLD_MKT).write_marker({"t": "now", "coid": "c1"})
    assert mech(st, OLD_MKT).already_ran() is True
    assert mech(st, NEW_MKT).already_ran() is False


def test_a_different_experiment_on_the_same_market_is_unspent(monkeypatch):
    st = Store()
    mech(st, NEW_MKT).write_marker({"t": "now", "coid": "c1"})
    assert mech(st, NEW_MKT).already_ran() is True
    monkeypatch.setenv("L5_EXPERIMENT_ID", "sub2.A-DIFFERENT-EXPERIMENT")
    assert mech(st, NEW_MKT).already_ran() is False


def test_history_is_preserved_not_overwritten():
    st = Store()
    mech(st, OLD_MKT).write_marker({"t": "t1", "coid": "c1"})
    mech(st, NEW_MKT).write_marker({"t": "t2", "coid": "c2"})
    runs = st.state[M.MARKER_KEY]["runs"]
    assert set(runs) == {"%s|%s" % (EXP, OLD_MKT), "%s|%s" % (EXP, NEW_MKT)}
    assert runs["%s|%s" % (EXP, OLD_MKT)]["coid"] == "c1"
    # both still refuse
    assert mech(st, OLD_MKT).already_ran() is True
    assert mech(st, NEW_MKT).already_ran() is True


# ------------------------------------------------ legacy (pre-v2) markers
def legacy(market):
    """Exactly what the 2026-10-18 run wrote: flat, market inside `plan`."""
    return {"t": "2026-10-18T23:20:52Z", "coid": "sub1-x",
            "stage": "SUBMITTING",
            "plan": {"market": market, "side": "UP", "qty": "0.01",
                     "price": "0.6400", "post_only": True}}


def test_a_legacy_marker_still_refuses_ITS_OWN_market():
    st = Store({M.MARKER_KEY: legacy(OLD_MKT)})
    assert mech(st, OLD_MKT).already_ran() is True, (
        "the experiment is unknown, so it MAY be ours: never guessed away")


def test_a_legacy_marker_does_not_block_a_different_market():
    """THE WHOLE POINT: the 2315z attempt must not block 0300z forever."""
    st = Store({M.MARKER_KEY: legacy(OLD_MKT)})
    assert mech(st, NEW_MKT).already_ran() is False


def test_a_legacy_marker_with_no_market_blocks_everything():
    """Ambiguous legacy state fails CLOSED, for every market."""
    st = Store({M.MARKER_KEY: {"t": "x", "coid": "c", "stage": "SUBMITTING"}})
    assert mech(st, NEW_MKT).already_ran() is True
    assert mech(st, OLD_MKT).already_ran() is True


def test_a_non_mapping_marker_blocks_everything():
    for junk in ("spent", 1, ["a"], True):
        st = Store({M.MARKER_KEY: junk})
        assert mech(st, NEW_MKT).already_ran() is True, junk


def test_a_v2_marker_with_unparseable_runs_blocks_everything():
    st = Store({M.MARKER_KEY: {"v": 2, "runs": "not-a-mapping"}})
    assert mech(st, NEW_MKT).already_ran() is True


def test_unreadable_state_counts_as_used():
    class Boom:
        @property
        def state(self):
            raise OSError("ledger unreadable")

    assert mech(Boom()).already_ran() is True


def test_a_legacy_marker_is_carried_forward_when_a_new_run_writes():
    """Migrating must not erase the old attempt's refusal."""
    st = Store({M.MARKER_KEY: legacy(OLD_MKT)})
    mech(st, NEW_MKT).write_marker({"t": "t2", "coid": "c2"})
    assert st.state[M.MARKER_KEY]["v"] == 2
    assert mech(st, OLD_MKT).already_ran() is True, "old market still spent"
    assert mech(st, NEW_MKT).already_ran() is True, "new market now spent"


def test_the_written_doc_records_its_own_identity():
    st = Store()
    mech(st, NEW_MKT).write_marker({"t": "t", "coid": "c"})
    doc = st.state[M.MARKER_KEY]["runs"]["%s|%s" % (EXP, NEW_MKT)]
    assert doc["experiment"] == EXP and doc["market"] == NEW_MKT
    assert doc["identity"] == "%s|%s" % (EXP, NEW_MKT)
    assert st.saves == 1


def test_the_marker_accessor_returns_only_this_runs_doc():
    st = Store()
    mech(st, OLD_MKT).write_marker({"t": "t1", "coid": "c1"})
    assert mech(st, NEW_MKT).marker() is None
    assert mech(st, OLD_MKT).marker()["coid"] == "c1"


# -------------------- a new market authorizes NOTHING by itself
def test_the_marker_gate_is_not_an_authorization():
    """Scoping only makes the pair UNSPENT. Arming is a separate owner act:
    the mechanism still checks mode, arming and the window separately."""
    src = open("/home/user/kalshi-arb-bot/src/l5_cancel_mechanism.py").read()
    for gate in ('check("mode_enabled"', 'check("transport_armed"',
                 'check("authorization_window_open"',
                 'check("one_shot_not_already_used"',
                 'claim_submission('):
        assert gate in src, gate


# ----------------------------------------------------------------------
# THE REAL PRODUCTION MARKER, read off l5-pmus-live on 2026-10-19T02:34:45Z.
#
# It is the COMPLETION doc and carries neither `market` nor `plan` -- those
# belonged to the SUBMITTING doc it overwrote. The market is still recoverable
# EXACTLY from `coid`, which is built as "mech-<market>-<epoch>".
# ----------------------------------------------------------------------
PROD_MARKER = {
    "t": "2026-10-18T23:27:34.440380+00:00",
    "coid": "mech-cpc-btc-updown-15m-2026-10-18-2315z-1792365652",
    "stage": "DONE",
    "venue_order_id": "D68KZ0SG10JR",
    "cancelled_by": "SELF_FALLBACK",
    "independent_cancellation_proven": False,
}


def test_the_real_production_marker_is_attributed_to_its_own_market():
    assert M._legacy_market(PROD_MARKER) == OLD_MKT


def test_the_real_production_marker_still_blocks_its_own_market():
    st = Store({M.MARKER_KEY: PROD_MARKER})
    assert mech(st, OLD_MKT).already_ran() is True


def test_the_real_production_marker_does_NOT_block_a_fresh_window():
    """THE OPERATIVE FACT. Before the rescoping this returned True for every
    market, which is what permanently spent the mechanism on this ledger."""
    st = Store({M.MARKER_KEY: PROD_MARKER})
    assert mech(st, NEW_MKT).already_ran() is False


def test_the_real_production_marker_is_carried_forward_on_the_next_write():
    st = Store({M.MARKER_KEY: PROD_MARKER})
    mech(st, NEW_MKT).write_marker({"t": "t2", "coid": "c2"})
    assert mech(st, OLD_MKT).already_ran() is True, "2315z must stay spent"
    assert mech(st, NEW_MKT).already_ran() is True, "0300z now spent too"
    runs = st.state[M.MARKER_KEY]["runs"]
    assert runs["?|%s" % OLD_MKT]["venue_order_id"] == "D68KZ0SG10JR"


@pytest.mark.parametrize("coid,expect", [
    ("mech-cpc-btc-updown-15m-2026-10-18-2315z-1792365652", OLD_MKT),
    ("mech-%s-1" % NEW_MKT, NEW_MKT),
    ("mech-a-b-c-999", "a-b-c"),           # hyphens in the slug survive
    ("mech-no-trailing-epoch", "?"),       # no digits -> ambiguous
    ("notmech-x-1", "?"),                  # wrong prefix -> ambiguous
    ("", "?"), ("mech--1", "?"),
])
def test_the_coid_market_recovery_is_exact_or_ambiguous(coid, expect):
    assert M._legacy_market({"coid": coid}) == expect


def test_an_explicit_market_beats_the_coid():
    assert M._legacy_market(
        {"market": NEW_MKT, "coid": "mech-%s-1" % OLD_MKT}) == NEW_MKT


def test_a_plan_market_beats_the_coid():
    assert M._legacy_market(
        {"plan": {"market": NEW_MKT}, "coid": "mech-%s-1" % OLD_MKT}) == NEW_MKT


def test_an_unattributable_legacy_doc_blocks_every_market():
    for doc in ({"t": "x", "stage": "DONE"},
                {"coid": "garbage"},
                {"plan": {"side": "UP"}}):
        st = Store({M.MARKER_KEY: doc})
        assert mech(st, NEW_MKT).already_ran() is True, doc
        assert mech(st, OLD_MKT).already_ran() is True, doc
