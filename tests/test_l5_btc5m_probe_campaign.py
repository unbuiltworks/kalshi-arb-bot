"""The campaign approval document: many NAMED windows under one arming,
bounded in code, and never looser than the one-window document on anything
that is not "one window". Plus the reconciliation stop it requires.
"""
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_behaviour_probe as P
from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 21, 18, 0, tzinfo=UTC)
ISSUED = T0 - dt.timedelta(minutes=10)


def slug(t):
    return "cpc-btc-updown-5m-" + t.strftime("%Y-%m-%d-%H%Mz")


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def doc(n=24, **over):
    starts = [T0 + dt.timedelta(minutes=5 * i) for i in range(n)]
    d = {"class": P.CAMPAIGN_CLASS,
         "acknowledgement": P.CAMPAIGN_ACKNOWLEDGEMENT,
         "entry_ev": "UNPROVEN", "markets": [slug(t) for t in starts],
         "issued_utc": iso(ISSUED),
         "expires_utc": iso(starts[-1] + dt.timedelta(minutes=5)),
         "max_window_loss_usd": "0.20", "max_campaign_loss_usd": "0.60",
         "nonce": "probe-camp-0001"}
    d.update(over)
    return d


def env(d=None, halt="0.60", single=None, **more):
    e = {P.CAMPAIGN_DOC_ENV: json.dumps(doc() if d is None else d)}
    if halt is not None:
        e[P.LOSS_HALT_ENV] = json.dumps({"max_loss_usd": halt})
    if single is not None:
        e[P.DOC_ENV] = json.dumps(single)
    e.update(more)
    return e


def read(market, now, e):
    return P.read_campaign_document(market, now, e)


def at(i, s=5):
    return T0 + dt.timedelta(minutes=5 * i, seconds=s)


def test_the_loss_halt_variable_name_is_the_executors():
    assert P.LOSS_HALT_ENV == X.CAMPAIGN_LOSS_HALT_ENV


def test_absent_is_absent():
    assert P.read_campaign_document(slug(T0), at(0), {}) == {
        "ok": False, "present": False, "reason": None}
    assert P.campaign_configured({}) is False


@pytest.mark.parametrize("i", [0, 1, 11, 23])
def test_every_listed_window_is_covered_while_it_is_open(i):
    r = read(slug(T0 + dt.timedelta(minutes=5 * i)), at(i), env())
    assert r["ok"] is True and r["campaign_windows"] == 24
    assert r["max_window_loss_usd"] == D("0.20")
    assert r["expires_utc"] == (T0 + dt.timedelta(
        minutes=5 * i + 5)).isoformat()


def test_a_window_not_in_the_list_is_not_covered():
    d = doc(4)
    later = T0 + dt.timedelta(minutes=20)
    assert read(slug(later), later + dt.timedelta(seconds=5),
                env(d))["reason"] == "PROBE_OTHER_MARKET"
    gap = doc(4)
    gap["markets"].pop(1)
    skipped = T0 + dt.timedelta(minutes=5)
    assert read(slug(skipped), skipped + dt.timedelta(seconds=5),
                env(gap))["reason"] == "PROBE_OTHER_MARKET"


def test_a_listed_window_is_covered_only_inside_its_own_five_minutes():
    m = slug(T0 + dt.timedelta(minutes=5))
    assert read(m, at(0), env())["reason"] == "PROBE_WINDOW_NOT_OPEN"
    assert read(m, at(2), env())["reason"] == "PROBE_EXPIRED"
    assert read(m, at(1), env())["ok"] is True


@pytest.mark.parametrize("over,reason", [
    ({"class": P.CLASS}, "PROBE_CLASS_MISMATCH"),
    ({"acknowledgement": P.ACKNOWLEDGEMENT}, "PROBE_NOT_ACKNOWLEDGED"),
    ({"entry_ev": "POSITIVE"}, "PROBE_EV_NOT_STATED_UNPROVEN"),
    ({"nonce": "short"}, "PROBE_NONCE_UNREADABLE"),
    ({"markets": []}, "PROBE_MARKETS_UNREADABLE"),
    ({"markets": "cpc-btc-updown-5m-"}, "PROBE_MARKETS_UNREADABLE"),
    ({"markets": ["cpc-btc-updown-5m-"]}, "PROBE_MARKET_NOT_A_BTC5M_WINDOW"),
    ({"markets": ["cpc-btc-updown-15m-2026-11-21-1800z"]},
     "PROBE_MARKET_NOT_A_BTC5M_WINDOW"),
    ({"max_window_loss_usd": "0.50"},
     "PROBE_LOSS_CEILING_ABOVE_CODE_MAXIMUM"),
    ({"max_window_loss_usd": "0"}, "PROBE_LOSS_CEILING_UNREADABLE"),
    ({"max_campaign_loss_usd": "1.51"},
     "PROBE_CAMPAIGN_LOSS_CEILING_ABOVE_CODE_MAXIMUM"),
    ({"max_campaign_loss_usd": None},
     "PROBE_CAMPAIGN_LOSS_CEILING_UNREADABLE"),
    ({"issued_utc": iso(T0 + dt.timedelta(hours=1))},
     "PROBE_ISSUED_IN_THE_FUTURE"),
    ({"issued_utc": iso(T0 - dt.timedelta(hours=7))}, "PROBE_DOCUMENT_STALE"),
    ({"expires_utc": iso(T0 + dt.timedelta(hours=3))},
     "PROBE_EXPIRY_NOT_THE_LAST_WINDOW_END"),
    ({"expires_utc": "2026-11-21 20:00"}, "PROBE_TIME_UNREADABLE"),
])
def test_each_fault_refuses(over, reason):
    assert read(slug(T0), at(0), env(doc(**over)))["reason"] == reason


def test_unknown_and_missing_fields_refuse():
    d = doc()
    d["series"] = "cpc-btc-updown-5m-"
    assert read(slug(T0), at(0), env(d))["reason"] == "PROBE_FIELD_UNKNOWN"
    d = doc()
    del d["max_campaign_loss_usd"]
    assert read(slug(T0), at(0), env(d))["reason"] == "PROBE_FIELD_MISSING"
    assert read(slug(T0), at(0), {P.CAMPAIGN_DOC_ENV: "{"})["reason"] \
        == "PROBE_DOCUMENT_UNREADABLE"


def test_more_than_24_windows_or_more_than_two_hours_is_refused():
    assert read(slug(T0), at(0), env(doc(25)))["reason"] \
        == "PROBE_TOO_MANY_WINDOWS"
    d = doc(2)
    late = T0 + dt.timedelta(hours=2)
    d["markets"][1] = slug(late)
    d["expires_utc"] = iso(late + dt.timedelta(minutes=5))
    assert read(slug(T0), at(0), env(d))["reason"] \
        == "PROBE_CAMPAIGN_TOO_LONG"


def test_the_list_must_ascend_with_no_repeat():
    d = doc(3)
    d["markets"][2] = d["markets"][1]
    assert read(slug(T0), at(0), env(d))["reason"] \
        == "PROBE_MARKETS_NOT_ASCENDING"


def test_it_cannot_be_issued_after_its_first_window():
    d = doc(issued_utc=iso(T0 + dt.timedelta(minutes=6)))
    assert read(slug(T0 + dt.timedelta(minutes=10)), at(2), env(d))[
        "reason"] == "PROBE_ISSUED_AFTER_ITS_FIRST_WINDOW"


@pytest.mark.parametrize("halt,reason", [
    (None, "PROBE_CAMPAIGN_NEEDS_THE_LOSS_HALT"),
    ("0", "PROBE_CAMPAIGN_NEEDS_THE_LOSS_HALT"),
    ("0.61", "PROBE_CAMPAIGN_LOSS_HALT_LOOSER_THAN_APPROVED"),
    ("1.51", "PROBE_CAMPAIGN_LOSS_HALT_LOOSER_THAN_APPROVED"),
])
def test_the_code_enforced_loss_halt_is_required_and_no_looser(halt, reason):
    assert read(slug(T0), at(0), env(halt=halt))["reason"] == reason


def test_a_tighter_loss_halt_is_fine():
    assert read(slug(T0), at(0), env(halt="0.40"))["ok"] is True


class Params:
    entry_mode = "STRADDLE"
    straddle_book_relative = True
    straddle_requote_ticks = 2
    straddle_max_window_loss_usd = D("0.20")
    straddle_leg_max_age_s = 240.0
    straddle_p_pair_natural = straddle_p_complete = None
    straddle_p_lone_win = None
    straddle_completion_pair_cost = D("0")
    late_favorite_window_s = 0


def test_evaluate_admits_under_the_campaign_document():
    r = P.evaluate(market=slug(T0), now=at(0), params=Params(), env=env())
    assert r["ok"] is True and r["class"] == P.CAMPAIGN_CLASS
    assert r["campaign_windows"] == 24


def test_both_documents_set_honours_neither():
    single = {"class": P.CLASS, "acknowledgement": P.ACKNOWLEDGEMENT,
              "entry_ev": "UNPROVEN", "market": slug(T0),
              "window_start_utc": iso(T0),
              "expires_utc": iso(T0 + dt.timedelta(minutes=5)),
              "issued_utc": iso(ISSUED), "max_window_loss_usd": "0.20",
              "nonce": "probe-0000001"}
    r = P.evaluate(market=slug(T0), now=at(0), params=Params(),
                   env=env(single=single))
    assert r["ok"] is False and r["reason"] == "PROBE_TWO_DOCUMENTS_SET"


def test_the_one_window_document_is_unchanged_without_a_campaign():
    single = {"class": P.CLASS, "acknowledgement": P.ACKNOWLEDGEMENT,
              "entry_ev": "UNPROVEN", "market": slug(T0),
              "window_start_utc": iso(T0),
              "expires_utc": iso(T0 + dt.timedelta(minutes=5)),
              "issued_utc": iso(ISSUED), "max_window_loss_usd": "0.20",
              "nonce": "probe-0000001"}
    r = P.evaluate(market=slug(T0), now=at(0), params=Params(),
                   env={P.DOC_ENV: json.dumps(single)})
    assert r["ok"] is True and r["class"] == P.CLASS
    assert "campaign_windows" not in r


def test_the_same_preconditions_apply():
    class Rate(Params):
        straddle_p_complete = 0.7
    r = P.evaluate(market=slug(T0), now=at(0), params=Rate(), env=env())
    assert r["reason"] == "PROBE_REFUSES_WHILE_AN_EV_RATE_IS_SET"
    r = P.evaluate(market=slug(T0), now=at(0), params=Params(),
                   env=env(**{P.RATE_ENVS[0]: "0.7"}))
    assert r["reason"] == "PROBE_REFUSES_WHILE_AN_EV_RATE_IS_SET"

    class Loose(Params):
        straddle_max_window_loss_usd = D("0.30")
    r = P.evaluate(market=slug(T0), now=at(0), params=Loose(), env=env())
    assert r["reason"] == "PROBE_LOSS_CEILING_LOOSER_THAN_APPROVED"


# ------------------------------------------------ the reconciliation stop

NOW = dt.datetime(2026, 11, 21, 19, 0, tzinfo=UTC)
M1, M2, M3 = (slug(T0), slug(T0 + dt.timedelta(minutes=5)),
              slug(T0 + dt.timedelta(minutes=50)))
CAMP = {P.CAMPAIGN_DOC_ENV: "{}"}          # SET is enough to switch it on


def own(results, ages):
    return RUN.OwnSettled.of(
        {m: D(v) for m, v in results.items()},
        {m: {"at": NOW - dt.timedelta(seconds=ages[m]), "pnl": D(v)}
         for m, v in results.items()})


def risk(results, day="2026-11-21"):
    return {"utc_day": "2026-11-21", "retired_results_utc_day": (
        None if results is None else {"utc_day": day, "results": results})}


def recon(o, r, e=CAMP):
    return RUN.campaign_reconciliation(o, r, NOW, e)


def test_no_campaign_document_no_check():
    assert recon(own({M1: "-0.04"}, {M1: 9999}), risk({}), {}) is None


def test_reconciled_windows_pass():
    r = recon(own({M1: "0.0195", M2: "-0.0408"}, {M1: 5000, M2: 3000}),
              risk({M1: "0.01950000", M2: "-0.04080000"}))
    assert r["ok"] is True and r["checked"] == sorted([M1, M2])


def test_within_tolerance_passes_and_beyond_it_stops():
    o = own({M1: "-0.0408"}, {M1: 3000})
    assert recon(o, risk({M1: "-0.0358"}))["ok"] is True       # 0.005
    r = recon(o, risk({M1: "-0.0200"}))
    assert r["ok"] is False and r["why"] == "CAMPAIGN_LEDGER_NOT_RECONCILED"
    assert r["failed"][M1].startswith("DIFFERS:")


def test_there_is_no_grace_a_pending_reconciliation_waits():
    """The owner's rule: do not trade through a window that has not
    reconciled yet, however recently it settled."""
    assert RUN.CAMPAIGN_RECON_GRACE_S == 0
    for age in (0, 30, 600, 2400):
        r = recon(own({M3: "-0.0408"}, {M3: age}), risk({M3: "-0.02"}))
        assert r["ok"] is False and r["within_grace"] == []
        r = recon(own({M3: "-0.0408"}, {M3: age}), risk({}))
        assert r["failed"] == {M3: "NOT_IN_THE_ACCOUNT_LEDGER"}
    assert recon(own({M3: "-0.0408"}, {M3: 5}),
                 risk({M3: "-0.0408"}))["ok"] is True


def test_a_window_the_account_ledger_never_booked_stops():
    r = recon(own({M1: "0.01"}, {M1: 2500}), risk({}))
    assert r["failed"] == {M1: "NOT_IN_THE_ACCOUNT_LEDGER"}


@pytest.mark.parametrize("r_", [risk(None), risk({}, day="2026-11-20")])
def test_nothing_published_stops_once_a_window_is_due(r_):
    assert recon(own({M1: "0.01"}, {M1: 2500}), r_)["ok"] is False
    assert recon(own({M1: "0.01"}, {M1: 1}), r_)["ok"] is False
    #: with nothing settled today there is nothing to reconcile
    assert recon(RUN.OwnSettled.of({}, {}), r_)["ok"] is True


def test_no_breakdown_is_not_a_pass():
    assert recon(D("0"), risk({}))["ok"] is False


def test_a_gain_that_differs_stops_too():
    r = recon(own({M1: "0.0195"}, {M1: 3000}), risk({M1: "0"}))
    assert r["ok"] is False


def _doc(results):
    return {"as_of_utc": NOW.isoformat(), "account_risk": {
        "readable": True, "utc_day": "2026-11-21",
        "utc_day_realized_usd": "-0.0213", "daily_halt_usd": "50",
        "lifetime_realized_usd": "-39.5158",
        "cumulative_loss_ceiling_usd": "75.00",
        "retired_results_utc_day": {"utc_day": "2026-11-21",
                                    "results": results}}}


def test_account_risk_refuses_on_an_unreconciled_campaign(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    o = own({M1: "-0.0408"}, {M1: 3000})
    r = RUN.account_risk(url="u", now=NOW, own_unaccounted=o,
                         fetch=lambda u: _doc({M1: "-0.0200"}))
    assert r["ok"] is False
    assert r["reason"] == "CAMPAIGN_LEDGER_NOT_RECONCILED"
    assert r["campaign_reconciliation"]["failed"]


def test_account_risk_is_untouched_without_a_campaign(monkeypatch):
    monkeypatch.delenv(P.CAMPAIGN_DOC_ENV, raising=False)
    o = own({M1: "-0.0408"}, {M1: 3000})
    r = RUN.account_risk(url="u", now=NOW, own_unaccounted=o,
                         fetch=lambda u: _doc({M1: "-0.0200"}))
    assert r.get("reason") != "CAMPAIGN_LEDGER_NOT_RECONCILED"
    assert "campaign_reconciliation" not in r


# ------------------------------ an earlier window not yet settled: waits

class _Ex:
    def __init__(self, pending=(), raises=False):
        self._pending, self._raises = list(pending), raises

    def pending_settlement(self):
        if self._raises:
            raise RuntimeError("ledger")
        return list(self._pending)


def _session(ex, market=M2, risk=None):
    s = RUN.Session.__new__(RUN.Session)
    s.ex, s.market = ex, market
    s.halt_fn = lambda: {"ok": True}
    s._own_unaccounted = lambda: RUN.OwnSettled.of({}, {})
    s._now = lambda: NOW
    box = {"risk": risk or {"ok": True, "reason": None,
                            "loss_room_usd": "0.95"}}
    s.risk_fn = lambda now, own: dict(box["risk"])
    s.exposure_ceiling = lambda: D("0.60")
    s.box = box
    return s


def _gates(ex, market=M2):
    return _session(ex, market).gates()


def test_under_a_campaign_an_unsettled_earlier_window_refuses(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    g = _gates(_Ex([M1]))
    assert g == {"ok": False, "pending": [M1],
                 "reason": "CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED"}


def test_the_window_being_traded_is_not_its_own_predecessor(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    assert _gates(_Ex([M2]))["ok"] is True
    assert _gates(_Ex([]))["ok"] is True


def test_an_unreadable_pending_set_refuses(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    assert _gates(_Ex(raises=True))["reason"] \
        == "CAMPAIGN_PENDING_SETTLEMENT_UNREADABLE"


def test_without_a_campaign_document_the_gate_is_as_before(monkeypatch):
    monkeypatch.delenv(P.CAMPAIGN_DOC_ENV, raising=False)
    assert _gates(_Ex([M1]))["ok"] is True
    assert _gates(_Ex(raises=True))["ok"] is True


# ------------------------- a window that waited is not joined part-way

GOOD = {"ok": True, "reason": None, "loss_room_usd": "0.95"}


def test_a_window_that_waited_on_settlement_is_sat_out_to_its_end(
        monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    ex = _Ex([M1])
    s = _session(ex)
    assert s.gates()["reason"] == "CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED"
    ex._pending = []                       # it settles mid-window
    g = s.gates()
    assert g == {"ok": False, "reason": "CAMPAIGN_WINDOW_SAT_OUT",
                 "first_refusal": "CAMPAIGN_PREVIOUS_WINDOW_NOT_SETTLED"}
    assert s.gates()["ok"] is False        # and stays out
    #: the NEXT window is a new session and starts clean
    assert _session(ex, market=M3).gates()["ok"] is True


def test_a_window_that_waited_on_reconciliation_is_sat_out(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    s = _session(_Ex([]), risk={"ok": False,
                                "reason": "CAMPAIGN_LEDGER_NOT_RECONCILED"})
    assert s.gates()["reason"] \
        == "ACCOUNT_RISK:CAMPAIGN_LEDGER_NOT_RECONCILED"
    s.box["risk"] = GOOD                   # reconciled a moment later
    assert s.gates()["reason"] == "CAMPAIGN_WINDOW_SAT_OUT"


def test_other_account_risk_refusals_do_not_sit_the_window_out(monkeypatch):
    """The brief portfolio-value refusal a window already rides through is
    unchanged: it resumes when the reading is fresh again."""
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    s = _session(_Ex([]), risk={
        "ok": False,
        "reason": "ACCOUNT_DRAWDOWN:PORTFOLIO_VALUE_UNAVAILABLE"})
    assert s.gates()["ok"] is False
    s.box["risk"] = GOOD
    assert s.gates()["ok"] is True


def test_an_unreadable_pending_set_sits_the_window_out(monkeypatch):
    monkeypatch.setenv(P.CAMPAIGN_DOC_ENV, "{}")
    ex = _Ex(raises=True)
    s = _session(ex)
    assert s.gates()["ok"] is False
    ex._raises = False
    assert s.gates()["reason"] == "CAMPAIGN_WINDOW_SAT_OUT"


def test_without_a_campaign_document_nothing_is_ever_sat_out(monkeypatch):
    monkeypatch.delenv(P.CAMPAIGN_DOC_ENV, raising=False)
    s = _session(_Ex([M1]), risk={
        "ok": False, "reason": "CAMPAIGN_LEDGER_NOT_RECONCILED"})
    assert s.gates()["ok"] is False
    s.box["risk"] = GOOD
    assert s.gates()["ok"] is True
