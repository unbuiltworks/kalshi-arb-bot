"""The behaviour probe: one named BTC5m window, EV stated unproven.

WHAT THESE PIN. The probe may admit a leg ONLY when the entry-EV gate refused
it for want of a rate, ONLY in the one market and five minutes its document
names, ONLY with the window loss ceiling in force, and ONLY while every other
protection is exactly what it was. It arms nothing.

Driven through the real runner book, the real `Btc5mExecutor`, the strict
Venue double and a ledger on disk. Nothing is submitted live.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from decimal import Decimal as D

import pytest

from src import l5_behaviour_probe as P
from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X

from tests.test_l5_btc5m_execution import (          # noqa: E402
    ARMED, MARKET, T0, Rig, armed, clean_env,        # noqa: F401
)
from tests.test_l5_btc5m_continuous_participation import (   # noqa: E402
    Rig6, rig, two_sided,                            # noqa: F401
)
from tests.test_l5_btc5m_bonereaper_mode import (    # noqa: E402
    LEG, LOSS_CAP, _held, _no_requote_leak, at, bonereaper, cancelled,  # noqa: F401
    gated, potential_worst, probe, px, realized_worst, stacking, whipsaw,
    working,
)

UTC = dt.timezone.utc
W0 = dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)      # MARKET's window
RATES = (X.STRADDLE_P_PAIR_NATURAL_ENV, X.STRADDLE_P_COMPLETE_ENV,
         X.STRADDLE_P_LONE_WIN_ENV)


def document(**over):
    doc = {"class": P.CLASS, "acknowledgement": P.ACKNOWLEDGEMENT,
           "entry_ev": "UNPROVEN", "market": MARKET,
           "window_start_utc": "2026-11-11T12:00:00Z",
           "expires_utc": "2026-11-11T12:05:00Z",
           "issued_utc": "2026-11-11T11:30:00Z",
           "max_window_loss_usd": "0.45", "nonce": "probe-0001-abcdef"}
    doc.update(over)
    return {k: v for k, v in doc.items() if v is not ...}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (P.DOC_ENV, X.STRADDLE_LEG_MAX_AGE_S_ENV,
              X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def rig(tmp_path):
    """Built the way the SERVICE builds it: with the probe function handed
    in. Overrides the plain rig the other suites use."""
    return Rig6(tmp_path, max_live_orders=6, ev_waiver_fn=P.evaluate)


@pytest.fixture
def unproven(monkeypatch, gated):
    """The probe profile with NO EV rate set: the normal gate refuses."""
    for k in RATES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(X.STRADDLE_LEG_MAX_AGE_S_ENV, "240")


@pytest.fixture
def approved(monkeypatch, unproven):
    monkeypatch.setenv(P.DOC_ENV, json.dumps(document()))


def put(monkeypatch, **over):
    monkeypatch.setenv(P.DOC_ENV, json.dumps(document(**over)))


# ============ 0. WITHOUT A DOCUMENT NOTHING CHANGES

def test_the_token_is_spelled_the_same_in_both_modules():
    assert C.EV_WAIVER_TOKEN == P.CLASS


def test_no_document_means_the_gate_refuses_exactly_as_before(rig, armed,
                                                              unproven):
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert "NOT_SET" in str(rep["why"])
    assert "behaviour_probe" not in rep, "an absent probe left a trace"


def test_the_waiver_field_has_no_default_and_no_env_reader(rig, two_sided,
                                                           monkeypatch):
    assert C.Params().straddle_ev_waiver is None
    monkeypatch.setenv("L5_BTC5M_STRADDLE_EV_WAIVER", P.CLASS)
    monkeypatch.setenv("L5_BTC5M_EV_WAIVER", P.CLASS)
    assert rig.ex.effective_params().straddle_ev_waiver is None
    #: attached in ONE place, through ONE helper
    ex = open(X.__file__, encoding="utf-8").read()
    assert ex.count("C.with_ev_waiver(") == 1
    assert "straddle_ev_waiver" not in ex
    ctl = open(C.__file__, encoding="utf-8").read()
    assert ctl.count("straddle_ev_waiver=EV_WAIVER_TOKEN") == 1


def test_an_executor_built_without_the_hook_ignores_a_valid_document(
        tmp_path, armed, approved):
    """The hook is INJECTED. Every executor not built by the service
    entrypoint -- every other lane, every other test -- has no probe at
    all, whatever the environment says."""
    plain = Rig6(tmp_path / "plain", max_live_orders=6)
    rep = plain.go(t=2.0, bid="0.50", ask="0.52")
    assert plain.venue.sent == []
    assert "behaviour_probe" not in rep
    assert "NOT_SET" in str(rep["why"])


def test_the_executor_does_not_import_the_probe_module():
    ex = open(X.__file__, encoding="utf-8").read()
    assert "l5_behaviour_probe" not in ex
    assert not hasattr(X, "PROBE")


def test_only_the_service_entrypoint_wires_the_hook():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = []
    for name in sorted(os.listdir(os.path.join(root, "src"))):
        if name.endswith(".py"):
            body = open(os.path.join(root, "src", name),
                        encoding="utf-8").read()
            if "ev_waiver_fn=PROBE" in body or "ev_waiver_fn=P." in body \
                    or "ev_waiver_fn = PROBE" in body:
                hits.append(name)
    assert hits == ["l5_btc5m_runner.py"], hits


# ============ 1. WITH A VALID DOCUMENT, THE ONE WINDOW TRADES -- BOUNDED

def test_a_valid_document_lets_the_named_window_quote_both_sides(rig, armed,
                                                                 approved):
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert {s["side"] for s in rig.venue.sent} == {C.UP, C.DOWN}, rep["why"]
    assert all(s["post_only"] and s["mode"] == "MAKER"
               and s["action"] == "BUY" and s["reason"] == LEG
               for s in rig.venue.sent)
    bp = rep["behaviour_probe"]
    assert bp["ok"] is True and bp["class"] == P.CLASS
    assert bp["market"] == MARKET
    assert "nonce" not in json.dumps(bp)


def test_the_evaluation_says_the_leg_was_admitted_unproven(rig, armed,
                                                           approved):
    """Never recorded as if the gate had passed it."""
    decision = C.decide(
        book=rig.book(bid="0.50", ask="0.52"), t_in_window_s=2.0,
        inventory=rig.ex.inventory(MARKET), resting=[],
        params=__import__("dataclasses").replace(
            rig.ex.effective_params(), straddle_ev_waiver=P.CLASS))
    rungs = decision["evaluation"]["entry_ev"]["rungs"]
    assert rungs and all(r["admitted"] and r["reason"] == P.CLASS
                         and r["ev_per_share"] is None
                         and r["gate_said"].endswith("_NOT_SET")
                         for r in rungs), rungs


def test_under_the_probe_the_adversary_is_still_held_to_the_ceiling(
        rig, armed, approved):
    """The whole justification: the loss ceiling stands in for the EV gate,
    so it must bind exactly as it does with the gate satisfied."""
    peak, pot, exp = whipsaw(rig)
    assert pot <= LOSS_CAP and peak <= LOSS_CAP
    assert peak > D("0.20"), "the probe never traded: %s" % peak
    assert exp <= D("2.40")


# ============ 2. EXACTLY ONE NAMED WINDOW, AND IT EXPIRES BY ITSELF

def test_another_market_is_not_covered(rig, armed, unproven, monkeypatch):
    put(monkeypatch, market="cpc-btc-updown-5m-2026-11-11-1205z",
        window_start_utc="2026-11-11T12:05:00Z",
        expires_utc="2026-11-11T12:10:00Z")
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep["behaviour_probe"]["reason"] == "PROBE_OTHER_MARKET"


def test_it_stops_admitting_the_instant_its_window_ends(rig, armed, approved):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    assert len(rig.venue.sent) == 2
    for o in list(rig.working()):
        rig.venue.fill(o)
    rig.now = W0 + dt.timedelta(seconds=300)
    rep = rig.ex.step(market=MARKET, book=rig.book(bid="0.50", ask="0.52"),
                      t_in_window_s=10.0, p_up="0.50", p_source="TEST")
    assert rep["behaviour_probe"]["reason"] == "PROBE_EXPIRED"
    assert len(rig.venue.sent) == 2, "it placed after its own expiry"


@pytest.mark.parametrize("over,why", [
    ({"expires_utc": "2026-11-11T12:10:00Z"}, "PROBE_EXPIRY_NOT_THE_WINDOW_END"),
    ({"expires_utc": "2026-11-12T12:05:00Z"}, "PROBE_EXPIRY_NOT_THE_WINDOW_END"),
    ({"window_start_utc": "2026-11-11T11:55:00Z"},
     "PROBE_WINDOW_DOES_NOT_MATCH_MARKET"),
    ({"issued_utc": "2026-11-11T05:00:00Z"}, "PROBE_DOCUMENT_STALE"),
    ({"issued_utc": "2026-11-11T12:03:00Z"}, "PROBE_ISSUED_IN_THE_FUTURE"),
    ({"issued_utc": "2026-11-11T12:00:00"}, "PROBE_TIME_UNREADABLE"),
    ({"market": "cpc-btc-updown-15m-2026-11-11-1200z"},
     "PROBE_MARKET_NOT_A_BTC5M_WINDOW"),
    ({"market": "cpc-btc-updown-5m-2026-11-11-1203z"},
     "PROBE_MARKET_NOT_A_BTC5M_WINDOW"),
    ({"class": "BEHAVIOUR_PROBE"}, "PROBE_CLASS_MISMATCH"),
    ({"acknowledgement": "ok"}, "PROBE_NOT_ACKNOWLEDGED"),
    ({"acknowledgement": P.ACKNOWLEDGEMENT.lower()}, "PROBE_NOT_ACKNOWLEDGED"),
    ({"entry_ev": "POSITIVE"}, "PROBE_EV_NOT_STATED_UNPROVEN"),
    ({"entry_ev": ...}, "PROBE_FIELD_MISSING"),
    ({"nonce": "x"}, "PROBE_NONCE_UNREADABLE"),
    ({"max_window_loss_usd": "0.46"}, "PROBE_LOSS_CEILING_ABOVE_CODE_MAXIMUM"),
    ({"max_window_loss_usd": "5"}, "PROBE_LOSS_CEILING_ABOVE_CODE_MAXIMUM"),
    ({"max_window_loss_usd": "0"}, "PROBE_LOSS_CEILING_UNREADABLE"),
    ({"max_window_loss_usd": "NaN"}, "PROBE_LOSS_CEILING_UNREADABLE"),
    ({"max_window_loss_usd": True}, "PROBE_LOSS_CEILING_UNREADABLE"),
    ({"max_windows": 3}, "PROBE_FIELD_UNKNOWN"),
    ({"p_pair_natural": "0.76"}, "PROBE_FIELD_UNKNOWN"),
])
def test_a_document_that_is_not_exactly_right_admits_nothing(
        rig, armed, unproven, monkeypatch, over, why):
    put(monkeypatch, **over)
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == [], (over, rep["why"])
    assert rep["behaviour_probe"]["ok"] is False
    assert rep["behaviour_probe"]["reason"] == why


@pytest.mark.parametrize("raw", ["{", "[]", "null", "\"YES\"", "1", "YES"])
def test_an_unreadable_document_admits_nothing(rig, armed, unproven,
                                               monkeypatch, raw):
    monkeypatch.setenv(P.DOC_ENV, raw)
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep["behaviour_probe"]["reason"] == "PROBE_DOCUMENT_UNREADABLE"


def test_before_its_window_opens_the_document_is_not_yet_valid():
    got = P.read_document(MARKET, W0 - dt.timedelta(seconds=1),
                          {P.DOC_ENV: json.dumps(document())})
    assert got["reason"] == "PROBE_WINDOW_NOT_OPEN"
    got = P.read_document(MARKET, W0, {P.DOC_ENV: json.dumps(document())})
    assert got["ok"] is True
    got = P.read_document(MARKET, W0 + dt.timedelta(seconds=299.9),
                          {P.DOC_ENV: json.dumps(document())})
    assert got["ok"] is True
    got = P.read_document(MARKET, W0.replace(tzinfo=None),
                          {P.DOC_ENV: json.dumps(document())})
    assert got["ok"] is False


# ============ 3. THE LOSS CEILING IS MANDATORY AND FAILS CLOSED

def test_without_the_loss_ceiling_the_probe_admits_nothing(rig, armed,
                                                           approved,
                                                           monkeypatch):
    monkeypatch.delenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV)
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep["behaviour_probe"]["reason"] == \
        "PROBE_NEEDS_WINDOW_LOSS_CEILING"


def test_a_ceiling_in_force_looser_than_the_approved_one_admits_nothing(
        rig, armed, approved, monkeypatch):
    put(monkeypatch, max_window_loss_usd="0.20")
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep["behaviour_probe"]["reason"] == \
        "PROBE_LOSS_CEILING_LOOSER_THAN_APPROVED"
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "0.20")
    rig.tick(2).go(t=4.0, bid="0.50", ask="0.52")
    assert len(rig.venue.sent) == 2
    assert potential_worst(rig) <= D("0.20")


def test_the_controller_refuses_a_waiver_that_arrives_without_its_conditions(
        rig, armed, unproven):
    """DEFENCE IN DEPTH. Hand `decide` the token directly, bypassing the
    probe module, with one condition removed each time. It admits nothing."""
    import dataclasses
    base = dataclasses.replace(rig.ex.effective_params(),
                               straddle_ev_waiver=P.CLASS)

    def places(params):
        d = C.decide(book=rig.book(bid="0.50", ask="0.52"),
                     t_in_window_s=2.0, inventory=rig.ex.inventory(MARKET),
                     resting=[], params=params)
        return [o for o in d["orders"] if o.get("op") == "PLACE"]

    assert len(places(base)) == 2                      # the control
    for change in (dict(straddle_max_window_loss_usd=D("0")),
                   dict(straddle_requote_ticks=0),
                   dict(straddle_ev_waiver="YES"),
                   dict(straddle_ev_waiver="behaviour_probe_ev_unproven"),
                   dict(straddle_ev_waiver=None),
                   dict(straddle_p_pair_natural=D("0.10"))):
        assert places(dataclasses.replace(base, **change)) == [], change


# ============ 4. NO INVENTED RATES; A NEGATIVE VERDICT STAYS NEGATIVE

@pytest.mark.parametrize("env", RATES)
def test_any_rate_being_set_makes_the_probe_refuse_outright(
        rig, armed, approved, monkeypatch, env):
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)
    monkeypatch.setenv(env, "0.10")
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep["behaviour_probe"]["reason"] == \
        "PROBE_REFUSES_WHILE_AN_EV_RATE_IS_SET"


def test_an_ev_the_gate_can_compute_and_finds_negative_is_never_waived():
    for reason in ("EV_NOT_POSITIVE", "EV_BELOW_FLOOR", "EV_UNREADABLE",
                   "ADVERSE_UNREADABLE", "PROBABILITIES_INCONSISTENT",
                   "PRICE_UNREADABLE", None, ""):
        p = C.Params(straddle_ev_waiver=P.CLASS)
        assert C._ev_waived(p, {"ok": False, "reason": reason}, True, 2,
                            D("0.45")) is False, reason
    assert C._ev_waived(C.Params(straddle_ev_waiver=P.CLASS),
                        {"ok": False, "reason": "P_PAIR_NATURAL_NOT_SET"},
                        True, 2, D("0.45")) is True


# ============ 5. ONE BEHAVIOUR: NOTHING ELSE MAY BE SWITCHED ON BESIDE IT

@pytest.mark.parametrize("env,val,why", [
    (X.COMPLETION_PAIR_COST_ENV, "0.90", "PROBE_REFUSES_COMPLETION"),
    (X.STRADDLE_REPRICE_TICKS_ENV, "1", "PROBE_REFUSES_INVENTORY_REPRICING"),
    (X.STRADDLE_REQUOTE_TICKS_ENV, "0", "PROBE_NEEDS_REQUOTE"),
    (X.STRADDLE_BOOK_RELATIVE_ENV, "NO", "PROBE_NEEDS_BOOK_RELATIVE"),
    (X.STRADDLE_LEG_MAX_AGE_S_ENV, "0", "PROBE_NEEDS_LEG_MAX_AGE"),
    (X.STRADDLE_LEG_MAX_AGE_S_ENV, "270", "PROBE_NEEDS_LEG_MAX_AGE"),
    (X.ENTRY_MODE_ENV, C.ACCUMULATE, "PROBE_NEEDS_STRADDLE_MODE"),
])
def test_a_configuration_outside_the_probes_shape_admits_nothing(
        rig, armed, approved, monkeypatch, env, val, why):
    monkeypatch.setenv(env, val)
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rep["behaviour_probe"]["reason"] == why, rep["behaviour_probe"]
    assert not [s for s in rig.venue.sent if s["reason"] == LEG]
    assert rep["behaviour_probe"]["ok"] is False


@pytest.mark.parametrize("field,val,why", [
    ("taker_allowed", True, "PROBE_REFUSES_TAKER"),
    ("completion_cross_enabled", True, "PROBE_REFUSES_TAKER"),
    ("late_favorite_window_s", D("30"), "PROBE_REFUSES_LATE_FAVORITE"),
    ("inventory_ranking_enabled", True, "PROBE_REFUSES_INVENTORY_RANKING"),
])
def test_taker_late_favourite_and_ranking_each_refuse(rig, two_sided,
                                                      unproven, field, val,
                                                      why):
    import dataclasses
    params = dataclasses.replace(rig.ex.effective_params(), **{field: val})
    assert P.preconditions(params, {"max_window_loss_usd": D("0.45")}) == why


# ============ 6. EVERY OTHER PROTECTION IS EXACTLY WHAT IT WAS

def test_it_does_not_arm_anything(rig, approved, monkeypatch):
    """No `armed` fixture: no owner approval, no arm token. A perfectly valid
    probe document reaches the venue with nothing."""
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep.get("behaviour_probe", {}).get("ok") in (True, None)


def test_the_operator_halt_still_stops_it(rig, armed, approved, monkeypatch):
    monkeypatch.setenv(X.HALT_ENV, "1")
    rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []


def test_the_account_cap_and_the_order_limit_still_bind(tmp_path, armed,
                                                        approved,
                                                        monkeypatch):
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.05")
    r = Rig6(tmp_path, max_live_orders=1, ev_waiver_fn=P.evaluate)
    r.go(t=2.0, bid="0.50", ask="0.52")
    assert len(r.venue.sent) <= 1
    spent = sum(D(s["price"]) * D(s["qty"]) for s in r.venue.sent)
    assert spent <= D("0.05")


def test_the_side_and_unmatched_caps_still_bind(rig, armed, approved,
                                                monkeypatch):
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.10")
    rig.go(t=2.0, bid="0.50", ask="0.52")
    for i in range(10):
        for o in working(rig, C.UP, LEG):
            rig.venue.fill(o)
        rep = at(rig, 5.0 + 2 * i, "0.50", "0.52")
    assert D(str(rig.ex.inventory(MARKET)[C.UP]["cost"])) <= D("0.10") + D("0.10")
    assert working(rig, C.UP) == []


def test_requotes_under_the_probe_are_still_by_exact_id(rig, armed, approved):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    old = set(rig.working())
    at(rig, 5.0, "0.56", "0.58")
    assert set(cancelled(rig)) == old
    assert len(rig.venue.sent) == 2


def test_the_module_holds_no_transport_and_writes_nothing():
    src = open(P.__file__, encoding="utf-8").read()
    body = src.split('"""', 2)[2]
    for word in ("submit(", ".cancel(", "open(", "os.environ[", "setenv",
                 "putenv", "requests", "urllib", "http", "socket"):
        assert word not in body, word
    assert re.findall(r"^(?:from|import) .*", body, re.M) == [
        "from __future__ import annotations", "import datetime as dt",
        "import json", "import os", "import re",
        "from decimal import Decimal as D, InvalidOperation",
        "from typing import Any, Dict, Mapping, Optional"]


def test_a_crash_inside_the_probe_is_a_refusal_not_an_admission():
    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("no")
    got = P.evaluate(market=MARKET, now=W0, params=Boom(),
                     env={P.DOC_ENV: json.dumps(document())})
    assert got["ok"] is False and got["reason"] == "PROBE_EVALUATION_ERROR"


def test_the_code_maximum_is_the_proven_figure_and_no_larger():
    assert P.MAX_WINDOW_LOSS_USD == D("0.45")
    assert P.MAX_WINDOW_LOSS_USD <= X.STRADDLE_MAX_WINDOW_LOSS_USD_CEILING
    assert P.WINDOW_S == 300


# ============ 7. THE WATCHDOG'S 300s AGAINST ACTUAL CANCELLATION TIMING

def test_the_watchdog_threshold_this_is_designed_against_is_the_real_one():
    from src import l5_watchdog as W
    from src import l5_btc5m_runner as R
    assert W.DEFAULT_MAX_ORDER_AGE_S == 300.0
    #: a leg is withdrawn on the first step at or after the max age, so its
    #: age at cancellation is under max age + one step
    assert P.MAX_LEG_AGE_S + R.STEP_S <= W.DEFAULT_MAX_ORDER_AGE_S - 50


def test_without_the_age_limit_a_quiet_book_leaves_a_leg_resting_298s(
        rig, armed, gated):
    """THE HAZARD, measured: nothing moves, so nothing is requoted, and the
    leg placed at t=2 is still there at t=298."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    first = set(rig.working())
    for t in range(7, 300, 5):
        rig.tick(5).go(t=float(t), bid="0.50", ask="0.52")
    assert set(rig.working()) == first and cancelled(rig) == []
    age = (rig.now - T0).total_seconds()
    assert age >= 290


def test_with_it_no_leg_is_ever_older_than_the_limit_plus_one_step(
        rig, armed, approved):
    """Stepped at the runner's real 5s cadence on a book that never moves.
    The oldest working order is measured before every step."""
    born, oldest, requotes = {}, 0.0, 0
    rig.go(t=2.0, bid="0.50", ask="0.52")
    for o in rig.working():
        born[o] = rig.now
    for t in range(7, 300, 5):
        rig.tick(5)
        for o in rig.working():
            oldest = max(oldest, (rig.now - born[o]).total_seconds())
        rig.go(t=float(t), bid="0.50", ask="0.52")
        for o in rig.working():
            if o not in born:
                born[o] = rig.now
                requotes += 1
    assert oldest <= P.MAX_LEG_AGE_S + 5, oldest
    assert oldest >= 230, "the limit never came into play: %s" % oldest
    assert len(cancelled(rig)) >= 2 and requotes >= 1
    ids = cancelled(rig)
    assert len(ids) == len(set(ids)) and set(ids) <= set(rig.venue.by_oid)


@pytest.mark.parametrize("raw,want", [("240", 240.0), ("999", 280.0),
                                      ("0", 0.0), ("-5", 0.0), ("x", 0.0),
                                      ("nan", 0.0), ("inf", 0.0), ("", 0.0)])
def test_the_leg_age_setting_is_clamped_and_fails_off(monkeypatch, raw, want):
    monkeypatch.setenv(X.STRADDLE_LEG_MAX_AGE_S_ENV, raw)
    assert X.straddle_leg_max_age_s() == want


# ============ 8. IT SHIPS, AND IT IS VISIBLE

def test_the_live_image_ships_the_probe_module():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    df = open(os.path.join(root, "deploy/l5_btc5m_live/Dockerfile")).read()
    assert "COPY src/l5_behaviour_probe.py src/l5_behaviour_probe.py" in df
    run = open(os.path.join(root, "src/l5_btc5m_runner.py")).read()
    assert "ev_waiver_fn=PROBE.evaluate" in run
    for src_file in (X.__file__, os.path.join(root, "src/l5_btc5m_runner.py")):
        body = open(src_file, encoding="utf-8").read()
        for mod in re.findall(r"^from src import (\w+)", body, re.M):
            assert "COPY src/%s.py" % mod in df, mod


def test_the_boot_report_says_whether_a_document_is_set_never_its_contents():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    s = open(os.path.join(root, "src/l5_btc5m_runner.py")).read()
    i = s.index('emit("L5_BTC5M_EFFECTIVE_CONFIG"')
    block = s[i:s.index('emit("L5_BTC5M_ARM"', i)]
    assert '"behaviour_probe_document_set": bool(' in block
    assert '"straddle_leg_max_age_s"' in block
    #: the document itself is never read into the report
    assert "PROBE.read_document" not in block
    assert "PROBE.evaluate" not in block
    assert re.search(r'"behaviour_probe_document_set": bool\(\s*'
                     r'\(os\.environ\.get\(PROBE\.DOC_ENV\) or ""\)'
                     r'\.strip\(\)\)', block)


# ============ 9. FOUND REVIEWING THE RELEASE: TWO WAYS ROUND, BOTH CLOSED

def test_the_rate_variable_names_match_the_executors():
    assert P.RATE_ENVS == RATES


@pytest.mark.parametrize("env", RATES)
def test_a_leftover_rate_the_gate_ignores_still_makes_the_probe_refuse(
        rig, armed, approved, monkeypatch, env):
    """Since the conditional-basis attestation, a rate set WITHOUT it reads
    as None -- so the effective params say 'no rate' while a number from an
    earlier campaign still sits in the environment. The probe must not run
    beside it."""
    monkeypatch.delenv(X.STRADDLE_P_BASIS_ENV, raising=False)
    monkeypatch.setenv(env, "0.76")
    assert getattr(rig.ex.effective_params(), {
        RATES[0]: "straddle_p_pair_natural", RATES[1]: "straddle_p_complete",
        RATES[2]: "straddle_p_lone_win"}[env]) is None
    rep = rig.go(t=2.0, bid="0.50", ask="0.52")
    assert rig.venue.sent == []
    assert rep["behaviour_probe"]["reason"] == \
        "PROBE_REFUSES_WHILE_AN_EV_RATE_IS_SET"


def test_the_age_limit_holds_while_the_venue_grid_cannot_be_verified(
        rig, armed, approved):
    """The venue-constraint hold deliberately cancels nothing. A leg must
    still be withdrawn at its age limit while the lane sits in that hold."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    first = set(rig.working())
    assert len(first) == 2
    bare = {"bid": "0.50", "ask": "0.52", "age_s": 0.1}     # no venue grid
    why = None
    for t in range(7, 262, 5):
        rep = rig.tick(5).ex.step(market=MARKET, book=bare,
                                  t_in_window_s=float(t), p_up="0.50",
                                  p_source="TEST")
        why = rep["why"]
        if (rig.now - T0).total_seconds() < 235:
            assert set(rig.working()) == first, t
    assert why == "VENUE_CONSTRAINTS_UNVERIFIED"
    assert set(cancelled(rig)) == first, cancelled(rig)
    assert rig.working() == []
    assert len(rig.venue.sent) == 2, "it re-quoted on an unverified grid"


def test_the_age_limit_does_not_need_the_requote_setting(rig, armed, gated,
                                                         monkeypatch):
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, "0")
    monkeypatch.setenv(X.STRADDLE_LEG_MAX_AGE_S_ENV, "60")
    rig.go(t=2.0, bid="0.50", ask="0.52")
    first = set(rig.working())
    for t in range(7, 80, 5):
        rig.tick(5).go(t=float(t), bid="0.50", ask="0.52")
    assert first <= set(cancelled(rig))


def test_with_no_age_limit_the_venue_hold_still_cancels_nothing(rig, armed,
                                                                gated):
    """Default unchanged: the hold's deliberate no-cancel is preserved."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    bare = {"bid": "0.50", "ask": "0.52", "age_s": 0.1}
    for t in range(7, 262, 5):
        rig.tick(5).ex.step(market=MARKET, book=bare, t_in_window_s=float(t),
                            p_up="0.50", p_source="TEST")
    assert cancelled(rig) == [] and len(rig.working()) == 2
