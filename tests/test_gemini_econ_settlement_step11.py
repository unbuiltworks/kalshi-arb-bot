"""STEP 11: the residual's settlement value, and the ledger's durability.

Two defects are pinned here, both found by tracing why `residSettle` had read
`None` on every arm since the collector started:

  1. `arm.finish()` was called without a settlement, so `settlement_leg` was
     always None and step 11 of the fill-sequence schema had never executed
     once -- leaving the ONE term large enough to set the sign of the loop's
     expectancy permanently unmeasured;
  2. `CohortState.save()` persisted hazard, spreads and toxicity but NOT the
     fill-sequence ledger, so the first fills and matched pairs the thesis
     turns on lived only in memory under restartPolicyType ALWAYS.
"""
import os
import tempfile

from src.gemini_execution.econ import fill_sequence as FS


def _seq(led, *, symbol, leg, price, t=1000.0):
    sq = led.candidate(cohort="btc5m", symbol=symbol, t=t, tte_s=200,
                       leg=leg, price=price)
    sq.note_rest([leg])
    sq.note_fill({"leg": leg, "price": price, "qty": "1"}, t=t + 1.0)
    return sq


def _led():
    return FS.FillSequenceLedger(arm="PP_F1")


def test_a_residual_is_labelled_with_the_published_outcome_and_signed_correctly():
    led = _led()
    _seq(led, symbol="S1", leg="YES", price="0.40")
    led.close_symbol("S1", residual_qty="1")
    row = [r for r in led.rows if r["symbol"] == "S1"][0]
    assert row["residual_settlement_value"] is None

    out = led.label_settlement("S1", "YES")
    assert out["labelled"] == 1
    # held YES, YES won: 1.00 - 0.40
    assert row["residual_settlement_value"] == "0.60"
    assert row["settlement_leg"] == "YES"

    led2 = _led()
    _seq(led2, symbol="S2", leg="YES", price="0.40")
    led2.close_symbol("S2", residual_qty="1")
    led2.label_settlement("S2", "NO")
    r2 = [r for r in led2.rows if r["symbol"] == "S2"][0]
    assert r2["residual_settlement_value"] == "-0.40"


def test_an_unknown_outcome_leaves_the_residual_UNLABELLED_rather_than_zero():
    led = _led()
    _seq(led, symbol="S1", leg="NO", price="0.30")
    led.close_symbol("S1", residual_qty="1")
    for bad in (None, "", "MAYBE", "BOTH", "DRAW", "yesno"):
        out = led.label_settlement("S1", bad)
        assert out["labelled"] == 0, bad
        assert out["why"]
    row = [r for r in led.rows if r["symbol"] == "S1"][0]
    assert row["residual_settlement_value"] is None
    # the venue publishes `resolutionSide: yes` in lower case; that IS an
    # outcome and must be accepted rather than discarded as unknown
    assert led.label_settlement("S1", "yes")["labelled"] == 1


def test_a_matched_pair_is_never_given_a_settlement_value():
    led = _led()
    sq = _seq(led, symbol="S1", leg="YES", price="0.40")
    sq.note_fill({"leg": "NO", "price": "0.59", "qty": "1"}, t=1005.0)
    led.close_symbol("S1", residual_qty="0")
    out = led.label_settlement("S1", "NO")
    assert out["labelled"] == 0 and out["skipped"] >= 1
    row = [r for r in led.rows if r["symbol"] == "S1"][0]
    assert row["matched"] is True
    assert row["residual_settlement_value"] is None


def test_labelling_is_idempotent_and_confined_to_its_own_contract():
    led = _led()
    _seq(led, symbol="S1", leg="YES", price="0.40")
    _seq(led, symbol="S2", leg="YES", price="0.40", t=2000.0)
    led.close_symbol("S1", residual_qty="1")
    led.close_symbol("S2", residual_qty="1")

    assert led.label_settlement("S1", "YES")["labelled"] == 1
    assert led.label_settlement("S1", "YES")["labelled"] == 0     # idempotent
    # the other contract is untouched: one window's outcome is not another's
    other = [r for r in led.rows if r["symbol"] == "S2"][0]
    assert other["residual_settlement_value"] is None


def test_the_funnel_reports_the_settlement_only_once_it_exists():
    led = _led()
    _seq(led, symbol="S1", leg="YES", price="0.40")
    led.close_symbol("S1", residual_qty="1")
    assert led.report()["residual_settlement_p50"] is None
    led.label_settlement("S1", "YES")
    assert led.report()["residual_settlement_p50"] == 0.60


def test_pending_settlements_excludes_the_window_that_just_closed():
    led = _led()
    _seq(led, symbol="OLD", leg="YES", price="0.40")
    _seq(led, symbol="FRESH", leg="YES", price="0.40", t=2000.0)
    led.close_symbol("OLD", residual_qty="1")
    led.close_symbol("FRESH", residual_qty="1")
    pend = led.unlabelled_symbols(exclude="FRESH")
    assert "FRESH" not in pend and "OLD" in pend
    led.label_settlement("OLD", "YES")
    assert led.unlabelled_symbols(exclude="FRESH") == []


def test_the_ledger_survives_a_restart_with_its_rows_and_labels():
    led = _led()
    _seq(led, symbol="S1", leg="YES", price="0.40")
    led.close_symbol("S1", residual_qty="1")
    led.label_settlement("S1", "YES")
    before = led.report()

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "btc5m.PP_F1.fills.json")
        led.save(p)
        back = FS.FillSequenceLedger.load(p, arm="PP_F1")

    assert back.arm == "PP_F1"
    after = back.report()
    for k in ("first_fills", "matched_pairs", "candidate_states",
              "residual_settlement_p50", "residual_rate_of_first_fills"):
        assert after[k] == before[k], k


def test_a_torn_or_missing_file_yields_an_empty_ledger_not_a_crash():
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "nope.json")
        assert FS.FillSequenceLedger.load(missing).rows == []
        torn = os.path.join(d, "torn.json")
        with open(torn, "w") as fh:
            fh.write('{"schema": "gemini_econ_fill_seq')
        assert FS.FillSequenceLedger.load(torn).rows == []
        wrong = os.path.join(d, "wrong.json")
        with open(wrong, "w") as fh:
            fh.write('{"schema": "something_else/v9", "rows": [{"a": 1}]}')
        assert FS.FillSequenceLedger.load(wrong).rows == []


def test_the_registry_persists_the_fill_ledger_per_arm():
    from src.gemini_execution.econ import registry as REG
    with tempfile.TemporaryDirectory() as d:
        a = REG.state_for("btc5m", arm="PP_F1", state_dir=d)
        _seq(a.fills, symbol="S1", leg="YES", price="0.40")
        a.fills.close_symbol("S1", residual_qty="1")
        a.fills.label_settlement("S1", "YES")
        saved = a.save()
        assert "fills" in saved["saved"], saved
        assert os.path.exists(os.path.join(d, "btc5m.PP_F1.fills.json"))

        REG.reset()
        b = REG.state_for("btc5m", arm="PP_F1", state_dir=d)
        assert b.fills.report()["first_fills"] == 1
        assert b.fills.report()["residual_settlement_p50"] == 0.60

        # a DIFFERENT arm on the same cohort must not inherit it: which fills
        # happened is a property of the strategy, not of the market
        c = REG.state_for("btc5m", arm="PP_F0", state_dir=d)
        assert c.fills.report()["first_fills"] == 0


# ------------------------------------------------------------------ collector

class _FakeArm:
    IS_ECON_ARM = True

    def __init__(self, label):
        self.label = label
        self.led = FS.FillSequenceLedger(arm=label)

    def pending_settlements(self, *, exclude=None, limit=4):
        return self.led.unlabelled_symbols(exclude=exclude, limit=limit)

    def label_settlement(self, symbol, leg):
        return self.led.label_settlement(symbol, leg)


def _armed(label, symbol, *, t=1000.0):
    a = _FakeArm(label)
    _seq(a.led, symbol=symbol, leg="YES", price="0.40", t=t)
    a.led.close_symbol(symbol, residual_qty="1")
    return a


def _run(arms, resolutions, just_closed=None):
    import asyncio

    from src.gemini_execution import metadata as MD
    from src.gemini_execution import shadow_soak as SS

    real = MD.resolution_for
    MD.resolution_for = lambda sym, **kw: resolutions[sym]
    try:
        return asyncio.run(SS._label_settlements(arms, just_closed=just_closed))
    finally:
        MD.resolution_for = real


def test_the_collector_labels_every_arm_from_ONE_read_of_the_contract():
    a, b = _armed("PP_F0", "S1"), _armed("PP_F1", "S1")
    out = _run([a, b], {"S1": {"is_terminal": True, "verdict": "SETTLED",
                               "resolutionSide": "yes",
                               "strike_check": {"available": True,
                                                "agrees": True}}})
    assert out["asked"] == 1 and out["settled"] == 1
    assert out["labelled"] == 2                       # one read, both arms
    for arm in (a, b):
        row = arm.led.rows[0]
        assert row["residual_settlement_value"] == "0.60"


def test_an_unconfirmed_reading_leaves_the_residual_UNLABELLED():
    a = _armed("PP_F0", "S1")
    out = _run([a], {"S1": {"is_terminal": False, "verdict": "UNCONFIRMED",
                            "resolutionSide": None, "strike_check": {}}})
    assert out["settled"] == 0 and out["unconfirmed"] == 1
    assert out["labelled"] == 0
    assert a.led.rows[0]["residual_settlement_value"] is None


def test_a_strike_disagreement_stops_rather_than_labelling():
    a = _armed("PP_F0", "S1")
    out = _run([a], {"S1": {"is_terminal": True, "verdict": "SETTLED",
                            "resolutionSide": "yes",
                            "strike_check": {"available": True,
                                             "agrees": False,
                                             "implied_by_strike": "no"}}})
    assert out["labelled"] == 0
    assert "strike_check disagrees" in out["symbols"]["S1"]["why"]
    assert a.led.rows[0]["residual_settlement_value"] is None


def test_the_contract_that_just_closed_is_never_asked():
    a = _FakeArm("PP_F0")
    _seq(a.led, symbol="OLD", leg="YES", price="0.40")
    _seq(a.led, symbol="FRESH", leg="YES", price="0.40", t=2000.0)
    a.led.close_symbol("OLD", residual_qty="1")
    a.led.close_symbol("FRESH", residual_qty="1")
    out = _run([a], {"OLD": {"is_terminal": True, "verdict": "SETTLED",
                             "resolutionSide": "no",
                             "strike_check": {"available": True,
                                              "agrees": True}}},
               just_closed="FRESH")
    assert list(out["symbols"]) == ["OLD"]
    assert out["labelled"] == 1


def test_a_read_that_raises_never_ends_the_window():
    a = _armed("PP_F0", "S1")

    class _Boom(dict):
        def __getitem__(self, k):
            raise RuntimeError("venue down")

    import asyncio

    from src.gemini_execution import metadata as MD
    from src.gemini_execution import shadow_soak as SS

    real = MD.resolution_for

    def _raise(sym, **kw):
        raise RuntimeError("venue down")

    MD.resolution_for = _raise
    try:
        out = asyncio.run(SS._label_settlements([a], just_closed=None))
    finally:
        MD.resolution_for = real
    assert out["labelled"] == 0
    assert "venue down" in out["symbols"]["S1"]["error"]
    assert a.led.rows[0]["residual_settlement_value"] is None
