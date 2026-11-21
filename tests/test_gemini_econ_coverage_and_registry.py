"""Coverage denominators, state that outlives a window, and pre-warming."""
import pytest

from src.gemini_execution.econ import coverage as CV
from src.gemini_execution.econ import prewarm as PW
from src.gemini_execution.econ import registry as REG
from src.gemini_execution.econ import spread_census as SC
from src.gemini_execution.econ import ws_flow as WF


@pytest.fixture(autouse=True)
def _clean():
    REG.reset()
    yield
    REG.reset()


# ------------------------------------------------------------- the denominator

def test_a_warmup_cycle_is_not_an_entry_refusal():
    """THE FAILURE THIS PREVENTS. Counting unvouched cycles as refusals turns
    'we were not watching yet' into 'the strategy rejects early windows' --
    opposite findings reported as the same number."""
    c = CV.CoverageLedger()
    for i in range(10):
        assert c.cycle(symbol="S", t=float(i), vouched=False,
                       quoting=True) == CV.WARMUP_EXCLUDED
    r = c.report()
    assert r["warmup_excluded_cycles"] == 10
    assert r["strategy_refusals"] == 0
    assert r["strategy_eligible_cycles"] == 0
    assert r["admission_rate_of_eligible"] is None     # not 0.0


def test_the_buckets_are_mutually_exclusive_and_sum_to_total():
    c = CV.CoverageLedger()
    for i in range(7):
        c.cycle(symbol="S", t=float(i), vouched=False, quoting=True)
    for i in range(7, 12):
        c.cycle(symbol="S", t=float(i), vouched=True, quoting=False)
    for i in range(12, 20):
        c.cycle(symbol="S", t=float(i), vouched=True, quoting=True)
        c.decision(admitted=(i == 12))
    r = c.report()
    assert r["buckets_are_exclusive"] is True
    assert (r["warmup_excluded_cycles"] + r["not_quoting_cycles"]
            + r["strategy_eligible_cycles"]) == r["total_cycles"] == 20
    assert r["entries_admitted"] + r["strategy_refusals"] == 8
    assert r["admission_rate_of_eligible"] == 1 / 8


def test_the_admission_rate_uses_eligible_not_total():
    """A rate over total cycles folds a coverage gap into a strategy verdict."""
    c = CV.CoverageLedger()
    for i in range(90):
        c.cycle(symbol="S", t=float(i), vouched=False, quoting=True)
    for i in range(90, 100):
        c.cycle(symbol="S", t=float(i), vouched=True, quoting=True)
        c.decision(admitted=(i < 95))
    r = c.report()
    assert r["admission_rate_of_eligible"] == 0.5      # 5 of 10 eligible
    assert r["warmup_share_of_total"] == 0.9


def test_warmup_is_split_by_whether_prewarming_happened():
    """'pre-warmed and still not enough' and 'never got the chance' are
    different problems with different fixes."""
    c = CV.CoverageLedger()
    c.cycle(symbol="S", t=1.0, vouched=False, quoting=True, prewarmed=True)
    c.cycle(symbol="S", t=2.0, vouched=False, quoting=True, prewarmed=False)
    r = c.report()
    assert r["warmup_prewarmed"] == 1 and r["warmup_not_prewarmed"] == 1


def test_time_to_vouched_is_recorded_per_contract():
    c = CV.CoverageLedger()
    c.cycle(symbol="S", t=100.0, vouched=False, quoting=True)
    c.cycle(symbol="S", t=145.0, vouched=True, quoting=True)
    assert c.report()["median_time_to_vouched_s"] == 45.0


# ------------------------------------------------- state outliving a window

def test_the_census_accumulates_across_windows():
    """THE DEFECT THIS FIXES. The arm is rebuilt per window; a census rebuilt
    with it can never reach a sufficient cell."""
    st = REG.state_for("BTC5m")
    for i in range(40):
        st.spreads.record(SC.observe(
            cohort="BTC5m", tte_s=250, prints_per_min=1.0,
            book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=float(i)))
        st.window_done()                      # simulate a rollover each time
    again = REG.state_for("BTC5m")            # a new arm asks for its state
    assert again is st
    assert again.spreads.report()["cells_sufficient"] == 1


def test_two_cohorts_never_share_state():
    a, b = REG.state_for("BTC5m"), REG.state_for("BTC15m")
    assert a is not b
    a.spreads.record(SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=1.0,
                                book={"yes": {"bid": "0.6", "ask": "0.65"}},
                                t=1.0))
    assert b.spreads.observations == 0


def test_state_survives_a_process_restart(tmp_path):
    d = str(tmp_path)
    st = REG.state_for("BTC5m", state_dir=d)
    for i in range(5):
        st.spreads.record(SC.observe(
            cohort="BTC5m", tte_s=250, prints_per_min=1.0,
            book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=float(i)))
    st.window_done()
    REG.reset()                                # the container restarts
    back = REG.state_for("BTC5m", state_dir=d)
    assert back.spreads.observations == 5


def test_a_failed_save_never_stops_the_collector():
    st = REG.state_for("BTC5m", state_dir="/proc/definitely/not/writable")
    st.window_done()                           # must not raise
    assert st.save_errors >= 1
    assert st.last_save_error


# ------------------------------------------------------------- the census key

def test_a_report_never_emits_a_cell_key_twice():
    """The duplicate line in the live log was two short-lived censuses, not a
    key collision. Pin the guarantee so a regression is caught here."""
    c = SC.SpreadCensus()
    for i in range(20):
        c.record(SC.observe(cohort="BTC5m", tte_s=250, prints_per_min=1.0,
                            book={"yes": {"bid": "0.60", "ask": "0.65"}},
                            t=float(i)))
    r = c.report()
    assert r["keys_unique"] is True
    assert len(r["cells"]) == 1
    assert list(r["cells"].values())[0]["n"] == 20


def test_the_key_dimensions_are_published():
    r = SC.SpreadCensus().report()
    assert r["key_dims"] == ["cohort", "tte_bucket", "price_bucket",
                             "flow_bucket"]


def test_differing_on_any_key_dimension_makes_a_different_cell():
    base = dict(cohort="BTC5m", tte_s=250, prints_per_min=1.0,
                book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=1.0)
    k = SC.cell_of(SC.observe(**base))
    assert SC.cell_of(SC.observe(**{**base, "cohort": "BTC15m"})) != k
    assert SC.cell_of(SC.observe(**{**base, "tte_s": 30})) != k
    assert SC.cell_of(SC.observe(**{**base, "prints_per_min": 50.0})) != k
    assert SC.cell_of(SC.observe(
        **{**base, "book": {"yes": {"bid": "0.05", "ask": "0.10"}}})) != k


# ------------------------------------------------------------- the sign

def test_a_rising_bid_is_favourable_and_a_falling_bid_is_adverse():
    up, down = SC.SpreadCensus(), SC.SpreadCensus()
    obs = dict(cohort="BTC5m", tte_s=250, prints_per_min=1.0,
               book={"yes": {"bid": "0.60", "ask": "0.65"}}, t=0.0)
    up.record(SC.observe(**obs))
    up.advance(now=40.0, bid="0.65")
    down.record(SC.observe(**obs))
    down.advance(now=40.0, bid="0.55")
    u = list(up.report()["cells"].values())[0]
    d = list(down.report()["cells"].values())[0]
    assert u["signed_markout_ticks_by_horizon_s"]["30"] == 5.0
    assert d["signed_markout_ticks_by_horizon_s"]["30"] == -5.0
    # the unsigned number cannot tell them apart -- which is why both exist
    assert (u["abs_touch_move_ticks_by_horizon_s"]["30"]
            == d["abs_touch_move_ticks_by_horizon_s"]["30"] == 5.0)
    assert u["adverse_share_by_horizon_s"]["30"] == 0.0
    assert d["adverse_share_by_horizon_s"]["30"] == 1.0


def test_the_perspective_is_stated_not_inferred():
    r = SC.SpreadCensus().report()
    assert r["markout_perspective"] == "MAKER_BUY_YES_AT_BID"
    assert "FAVOURABLE" in r["markout_sign_note"]


# ------------------------------------------------------------- pre-warming

class _Ws:
    def __init__(self):
        self.watched, self.dropped = [], []

    def watch(self, s):
        self.watched.append(s)

    def drop(self, s):
        self.dropped.append(s)


def test_the_prewarmer_opens_upcoming_contracts_and_is_idempotent():
    p = PW.Prewarmer(cohort_name="BTC5m", prefix="BTC05M", minutes=5)
    ws = _Ws()
    t = 1758372000.0
    first = p.pump(ws, now=t)
    assert len(first["opened_now"]) >= 2       # current plus lookahead
    again = p.pump(ws, now=t)                  # same instant, same grid
    assert again["opened_now"] == []           # nothing re-opened


def test_the_prewarmer_rolls_forward_with_the_grid():
    """As the clock advances past a contract boundary the next contract must
    be opened -- that is the whole point of looking ahead."""
    p = PW.Prewarmer(cohort_name="BTC5m", prefix="BTC05M", minutes=5)
    ws = _Ws()
    t = 1758372000.0
    p.pump(ws, now=t)
    later = p.pump(ws, now=t + 300.0)          # one full BTC5m window on
    assert later["opened_now"], "the grid advanced and nothing was pre-warmed"


def test_the_prewarmer_is_bounded():
    p = PW.Prewarmer(cohort_name="BTC5m", prefix="BTC05M", minutes=5)
    ws = _Ws()
    for i in range(50):
        p.pump(ws, now=1758372000.0 + i * 300)
    assert len(p.watched) <= PW.MAX_WATCHED


def test_the_prewarmer_never_raises_on_a_broken_socket():
    class Boom:
        def watch(self, s):
            raise RuntimeError("socket down")

        def drop(self, s):
            raise RuntimeError("nope")

    p = PW.Prewarmer(cohort_name="BTC5m", prefix="BTC05M", minutes=5)
    got = p.pump(Boom(), now=1758372000.0)     # must not raise
    assert got["errors"] >= 1


def test_prewarm_lead_is_measured_not_assumed():
    p = PW.Prewarmer(cohort_name="BTC5m", prefix="BTC05M", minutes=5)
    p.watched["GEMI-X-UP"] = 100.0
    assert p.was_prewarmed("GEMI-X-UP", entered_at=130.0) is True
    assert p.was_prewarmed("GEMI-X-UP", entered_at=101.0) is False
    assert p.was_prewarmed("GEMI-UNSEEN-UP", entered_at=999.0) is False


# --------------------------------------------- the adaptive vouched window

class _B:
    def __init__(self, ready_since, trades):
        self.ready_since, self.ready = ready_since, True
        self._t = trades

    def prints_since(self, t0):
        return [x for x in self._t if x["t"] > t0]


class _F:
    def __init__(self, b):
        self._books = {"S": b}

    def watch(self, s):
        pass


def test_the_window_narrows_to_what_the_socket_can_attest():
    """Demanding 120s from a 45-second-old view discards real evidence to
    satisfy a number. Provenance is the interval, not its length."""
    t = 1000.0
    src = WF.WsFlowSource(feed=_F(_B(t - 45, [
        {"resting_side": "bid", "size": "1", "price": "0.6", "t": t - 10}])))
    got = src.flow("S", window_s=120, min_window_s=30, now=t)
    assert got["vouched"] is True
    assert got["window_requested_s"] == 120.0
    assert got["window_used_s"] == 45.0
    assert got["truncated_by_view"] is True


def test_below_the_floor_nothing_is_claimed():
    t = 1000.0
    src = WF.WsFlowSource(feed=_F(_B(t - 10, [])))
    got = src.flow("S", window_s=120, min_window_s=30, now=t)
    assert got["vouched"] is False and got["prints"] is None
    assert "10.0s of continuous view" in got["why"]


def test_a_full_view_is_not_truncated():
    t = 1000.0
    src = WF.WsFlowSource(feed=_F(_B(t - 600, [])))
    got = src.flow("S", window_s=120, min_window_s=30, now=t)
    assert got["window_used_s"] == 120.0
    assert got["truncated_by_view"] is False


# ------------------------------- the accounting bug the first live run found

def test_a_warmup_cycle_can_never_be_counted_as_a_strategy_refusal():
    """REPRODUCES THE LIVE DEFECT. The first run printed

        total=2 vouched=0 warmup_excl=2 eligible=0 refusals=4

    which is arithmetically impossible: four refusals from zero eligible
    cycles. The caller was recording an outcome on warm-up cycles AND once per
    proposed rung rather than once per cycle. The ledger now refuses both.
    """
    c = CV.CoverageLedger()
    b = c.cycle(symbol="S", t=1.0, vouched=False, quoting=True)
    assert b == CV.WARMUP_EXCLUDED
    for _ in range(4):
        assert c.decision(False, bucket=b) is False
    r = c.report()
    assert r["strategy_eligible_cycles"] == 0
    assert r["strategy_refusals"] == 0
    assert r["decisions_refused_wrong_bucket"] == 4
    assert r["accounting_consistent"] is True


def test_one_cycle_cannot_record_two_outcomes():
    """A cycle proposing two rungs is still ONE cycle."""
    c = CV.CoverageLedger()
    b = c.cycle(symbol="S", t=1.0, vouched=True, quoting=True)
    assert c.decision(True, bucket=b) is True
    assert c.decision(False, bucket=b) is False
    r = c.report()
    assert r["entries_admitted"] == 1 and r["strategy_refusals"] == 0
    assert r["decisions_refused_over_count"] == 1
    assert r["accounting_consistent"] is True


def test_the_consistency_invariant_is_reported_every_time():
    c = CV.CoverageLedger()
    for i in range(5):
        b = c.cycle(symbol="S", t=float(i), vouched=True, quoting=True)
        c.decision(admitted=(i % 2 == 0), bucket=b)
    r = c.report()
    assert r["accounting_consistent"] is True
    assert r["eligible_cycles_without_a_decision"] == 0
    assert r["entries_admitted"] + r["strategy_refusals"] == 5


def test_an_eligible_cycle_with_no_proposal_is_not_a_refusal():
    """The policy may simply not propose anything. That is not the strategy
    refusing an entry, and must not inflate the refusal count."""
    c = CV.CoverageLedger()
    for i in range(3):
        c.cycle(symbol="S", t=float(i), vouched=True, quoting=True)
    r = c.report()
    assert r["strategy_eligible_cycles"] == 3
    assert r["strategy_refusals"] == 0
    assert r["eligible_cycles_without_a_decision"] == 3
    assert r["accounting_consistent"] is True
