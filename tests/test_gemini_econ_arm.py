"""GEMINI_ECON_V1: the challenger, and the invariants that keep it honest."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution.econ import actions as AC
from src.gemini_execution.econ import arm as ARM
from src.gemini_execution.econ import policy as PO

SYM15 = "GEMI-BTC15M2609201200-UP"
SYM5 = "GEMI-BTC05M2609201200-UP"

BOOK = {"readable": True, "identity_consistent": True,
        "yes": {"bid": "0.60", "ask": "0.65"},
        "no": {"bid": "0.35", "ask": "0.40"}}
FLOW = {"prints": [{"side": "buy", "qty": "1", "price": "0.61"},
                   {"side": "sell", "qty": "2", "price": "0.60"},
                   {"side": "buy", "qty": "1", "price": "0.61"},
                   {"side": "sell", "qty": "1", "price": "0.60"},
                   {"side": "buy", "qty": "1", "price": "0.61"}]}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The arm must be decidable from a supplied book alone."""
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})


class FakeWs:
    """A WS flow source with no socket.

    The gates read the MAINTAINED socket now, so a test that wants to drive
    them must supply socket-shaped flow. `vouched=None` means "behave as if the
    socket cannot vouch", which must refuse.
    """

    def __init__(self, prints=None, vouched=True, why=None):
        self._prints = prints
        self.vouched = vouched
        self.why = why
        self.watched = []
        self.dropped = []

    def watch(self, symbol):
        self.watched.append(symbol)

    def drop(self, symbol):
        self.dropped.append(symbol)

    def flow(self, symbol, *, window_s, now=None):
        if not self.vouched:
            return {"vouched": False, "prints": None,
                    "why": self.why or "socket cannot vouch",
                    "window_s": window_s}
        rows = [{"side": p["side"], "qty": p["qty"], "price": p.get("price"),
                 "t": i} for i, p in enumerate(self._prints or [])]
        return {"vouched": True, "prints": rows, "n_prints": len(rows),
                "n_directional": len(rows), "undirected": 0,
                "window_s": window_s, "why": None}


def _arm(cohort="BTC15m", symbol=SYM15, ws=None, prints=None):
    return ARM.GeminiEconArm(
        symbol=symbol, cohort_prefix="GEMI-BTC15M" if cohort == "BTC15m"
        else "GEMI-BTC05M", policy="GEMINI_RECYCLE", cohort_name=cohort,
        ws=(ws if ws is not None
            else FakeWs(prints if prints is not None else TWO_SIDED)))


TWO_SIDED = [{"side": "buy", "qty": "1", "price": "0.61"},
             {"side": "sell", "qty": "2", "price": "0.60"},
             {"side": "buy", "qty": "1", "price": "0.61"},
             {"side": "sell", "qty": "1", "price": "0.60"},
             {"side": "buy", "qty": "1", "price": "0.61"}]


def _step(a, tte=700, book=BOOK, flow=FLOW, symbol=SYM15):
    return a.step(symbol=symbol, tte=tte, book=book, depth=None, flow=flow,
                  policy="GEMINI_RECYCLE")


def test_btc15m_inherits_the_reviewed_timing_correction():
    """Without it the [0,300] band withholds the surface for the whole
    900->300 region and the cohort policy could never be exercised."""
    a = _arm("BTC15m")
    assert a.report()["spec_label"] == "L5_ADAPTED_GEMINI_BTC15M_V2"
    r = _step(a, tte=700)
    assert len(r["model_a_wanted"]) > 0


def test_btc5m_uses_the_frozen_default_spec_unchanged():
    a = _arm("BTC5m", SYM5)
    assert a.report()["spec_label"] == "FROZEN_DEFAULT"
    assert a.sp is None


def test_the_arm_never_invents_a_rung_model_a_did_not_ask_for():
    """ADAPTATION_ADDED is how an adaptation gets rewritten as an L5
    discovery. It must stay at zero: this arm only suppresses or re-routes."""
    a = _arm()
    for tte in (800, 700, 600, 500, 400):
        r = _step(a, tte=tte)
        assert len(r["econ_acted"]) <= len(r["model_a_wanted"])
    assert a.divergences.get(ARM.ADDED, 0) == 0


def test_model_a_is_recorded_before_the_economics_touch_it():
    a = _arm()
    r = _step(a)
    assert r["model_a_wanted"]
    # every acted rung traces back to something Model A proposed
    wanted_keys = {(w.get("leg"), w.get("rung_index"))
                   for w in r["model_a_wanted"]}
    for got in r["econ_acted"]:
        assert (got.get("leg"), got.get("rung_index")) in wanted_keys


def test_reduce_and_exit_never_enter_an_l5_field():
    a = _arm()
    r = _step(a)
    for d in r["decisions"]:
        assert d["l5_action"] in AC.L5_LABELS
        assert d.get("gemini_disposition") in (None,) + AC.DISPOSITIONS


def test_a_toxic_window_suppresses_entry_and_names_the_gate():
    toxic = ([{"side": "buy", "qty": "500", "price": "0.61"}]
             + [{"side": "buy", "qty": "5", "price": "0.61"}] * 4)
    a = _arm(prints=toxic)
    r = _step(a)
    assert r["econ_acted"] == []
    assert a.divergences.get(ARM.SUPPRESSED, 0) > 0
    gates = {g for d in r["decisions"] for g in (d.get("failed_gates") or [])}
    assert "FLOW_REVERSIBLE" in gates or "NOT_CONCENTRATED" in gates


def test_the_cohort_mode_closes_entry_before_expiry():
    a = _arm()
    r = _step(a, tte=100)          # BTC15m: INVENTORY mode, no new first fills
    assert r["mode"]["mode"] == PO.INVENTORY
    assert r["econ_acted"] == []


def test_an_unreadable_book_stops_the_arm_rather_than_quoting():
    a = _arm()
    r = _step(a, book={"readable": False})
    assert r["econ_acted"] == []


def test_a_negative_completion_is_vetoed_and_counted():
    """Model A is handed a held leg it wants to complete at a losing price."""
    a = _arm()
    a.inv.apply_fill("YES", "0.01", "0.63", source="TEST")
    bad = {"readable": True, "identity_consistent": True,
           "yes": {"bid": "0.50", "ask": "0.55"},
           "no": {"bid": "0.45", "ask": "0.50"}}       # complement ~0.45-0.50
    _step(a, tte=500, book=bad)
    board = a.board.report()
    # nothing was completed above the ceiling
    assert board["PAIRS_ABOVE_CEILING_defect_counter"] == 0
    if board["completions_considered"]:
        assert board["completions_vetoed"] == board["completions_considered"]


def test_complement_opportunities_are_split_by_acceptability():
    a = _arm()
    a.inv.apply_fill("YES", "0.01", "0.63", source="TEST")
    _step(a, tte=500)
    b = a.board.report()
    assert b["complement_opportunities"] >= b[
        "acceptable_complement_opportunities"]


def test_the_arm_reports_a_full_scoreboard_with_the_primary_metric():
    a = _arm()
    _step(a)
    r = a.report()["scoreboard"]
    for key in ("PRIMARY_net_cents_per_capital_dollar_minute",
                "PAIRS_ABOVE_CEILING_defect_counter",
                "acceptable_complement_opportunities",
                "maker_to_maker_completion_rate",
                "rebate_accrued_NOT_INCOME",
                "rebate_credited_venue_record",
                "capital_dollar_minutes"):
        assert key in r


def test_the_arm_holds_no_transport():
    src = open(ARM.__file__).read()
    for forbidden in ("ws_session", "order.place", "private_key", "api_key",
                      "Driver.place"):
        assert forbidden not in src


def test_an_unknown_contract_family_is_refused_by_the_guard():
    """An existing safety property, asserted here so the challenger cannot be
    the thing that loosens it."""
    with pytest.raises(CP.ProbeStop):
        ARM.GeminiEconArm(symbol=SYM5, cohort_prefix="GEMI-XXX",
                          policy="GEMINI_RECYCLE", cohort_name="BTC5m")


def test_an_unknown_cohort_yields_no_policy_and_the_arm_withdraws():
    """A KNOWN contract family with a cohort we have written no policy for
    must inherit nothing -- the TTE_PROVENANCE_1 defect, guarded at the arm."""
    a = ARM.GeminiEconArm(symbol=SYM5, cohort_prefix="GEMI-BTC05M",
                          policy="GEMINI_RECYCLE", cohort_name="DOGE1m")
    assert a.pol is None
    r = _step(a, symbol=SYM5)
    assert r["mode"]["mode"] == PO.WITHDRAW
    assert r["econ_acted"] == []


def test_the_hazard_survives_a_restart_through_the_arm(tmp_path):
    p = str(tmp_path / "hz.json")
    a = ARM.GeminiEconArm(symbol=SYM15, cohort_prefix="GEMI-BTC15M",
                          policy="GEMINI_RECYCLE", cohort_name="BTC15m",
                          hazard_path=p)
    _step(a)
    a.hazard.save(p)
    b = ARM.GeminiEconArm(symbol=SYM15, cohort_prefix="GEMI-BTC15M",
                          policy="GEMINI_RECYCLE", cohort_name="BTC15m",
                          hazard_path=p)
    assert b.hazard.cells == a.hazard.cells


# --------------------------------------------- flow provenance (WS re-source)

def test_the_gates_read_the_socket_and_not_the_polled_tape():
    """`habitat.public_flow` is labelled EVIDENCE ONLY. A rich polled tape must
    NOT be able to admit an entry when the socket says nothing."""
    a = _arm(ws=FakeWs([], vouched=True))
    rich = {"prints": [{"side": "buy", "qty": "1"},
                       {"side": "sell", "qty": "1"}] * 10}
    r = _step(a, flow=rich)
    assert r["econ_acted"] == []
    gates = {g for d in r["decisions"] for g in (d.get("failed_gates") or [])}
    assert "FLOW_REVERSIBLE" in gates      # the socket saw nothing; poll ignored


def test_unvouched_coverage_refuses_and_never_falls_back():
    a = _arm(ws=FakeWs(vouched=False, why="view began after the interval"))
    r = _step(a)
    assert r["econ_acted"] == []
    assert r["ws_flow"]["vouched"] is False
    # the gate is UNKNOWN rather than FAILED -- "could not measure" and
    # "measured and rejected" are different facts -- and unknown still refuses
    unknown = {g for d in r["decisions"] for g in (d.get("unknown_gates") or [])}
    assert "FLOW_REVERSIBLE" in unknown
    reasons = " ".join(str((d.get("gate_reasons") or {}).get("FLOW_REVERSIBLE"))
                       for d in r["decisions"])
    assert "UNVOUCHED" in reasons


def test_vouched_socket_flow_admits_the_same_window_the_poll_could_not():
    a = _arm(ws=FakeWs(TWO_SIDED, vouched=True))
    r = _step(a, flow={"prints": []})       # polled tape empty, socket rich
    assert r["ws_flow"]["vouched"] is True
    assert len(r["econ_acted"]) > 0


def test_the_arm_watches_its_symbol_on_the_socket():
    ws = FakeWs(TWO_SIDED)
    a = _arm(ws=ws)
    _step(a)
    assert SYM15 in ws.watched
