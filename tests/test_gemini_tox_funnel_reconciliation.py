"""P0: why ECON_FUNNEL_MO and ECON_TOX disagreed about YES markout.

THE CONTRADICTION.

    ECON_FUNNEL_MO (cohort A)  YES n=48  markout_p50_60s = +1.5
    ECON_TOX       (persisted) YES n=6   toxic_share     = 1.00

TOXIC is defined as markout@60s < 0 for the leg that filled, so on any shared
row these cannot both be true. They were never shared rows.

THE CAUSE, pinned below. `ToxicityDataset` rows are stamped `t = time.time()`
in the cycle the fill was CONSUMED. The labelling loop keyed on the fill
sequence's `t_candidate` -- the cycle the rung was ADMITTED, necessarily at
least one 8s cycle earlier, because a rung must rest before any print can
reach it. Matching was allowed within 12 seconds, so only fills that arrived
within roughly 1.5 cycles of admission were ever labelled. That is a
NON-RANDOM SUBSAMPLE, and it is biased in the worst possible direction: a
rung that fills almost immediately is a rung run over by fast one-directional
flow, which is exactly the adversely selected one.

Two further defects in the same loop, both pinned here: the nearest-match
fallback could attach one fill's outcome to a DIFFERENT fill's features, and
the candidate map was built once and never refreshed, so a row labelled
inside the loop could be relabelled by a later fill.

There is exactly ONE markout computation in the system -- the toxicity label
COPIES `markout_ticks_by_horizon_s["60"]` from the fill row -- so a sign
inversion was never possible. The stores could only ever disagree about WHICH
ROWS they hold, and they did.
"""
import pytest

from src.gemini_execution.econ import prefill as PF


CYCLE_S = 8.0
#: a realistic epoch stamp. 0.0 is falsy and was itself a latent defect.
T0 = 1789000000.0
OLD_TOLERANCE_S = 12.0


def tox_row(ds, *, t, leg="YES", symbol="S", price="0.40"):
    return ds.add(features={"schema": "x", "fill_t": t, "leg": leg},
                  cohort="btc5m", arm="PP_F0", symbol=symbol, leg=leg,
                  price=price, t=t)


def fill_row(*, seq_id, t_candidate, first_fill_t, markout_60s, leg="YES",
             symbol="S"):
    return {"seq_id": seq_id, "symbol": symbol, "first_fill": True,
            "first_fill_leg": leg, "t_candidate": t_candidate,
            "first_fill_t": first_fill_t,
            "markout_ticks_by_horizon_s": {"60": markout_60s},
            "complement_filled": False, "max_adverse_markout_ticks": -1.0}


# ------------------------------------------------- the defect, reconstructed
def _legacy_join(tox_rows, fill_rows, symbol="S"):
    """The join exactly as it was, so the bias is demonstrated and not
    asserted from memory."""
    byt = {round(float(r.get("t") or 0), 1): r for r in tox_rows
           if r.get("outcome") is None and r.get("symbol") == symbol}
    labelled = []
    for row in fill_rows:
        if row.get("symbol") != symbol or not row.get("first_fill"):
            continue
        key = round(float(row.get("t_candidate") or 0), 1)
        tgt = byt.get(key)
        if tgt is None and byt:
            k = min(byt, key=lambda x: abs(x - key))
            tgt = byt[k] if abs(k - key) <= OLD_TOLERANCE_S else None
        if tgt is None:
            continue
        PF.ToxicityDataset.label(
            tgt, markout_60s=(row["markout_ticks_by_horizon_s"]).get("60"),
            complement_filled=row["complement_filled"],
            max_adverse_ticks=row["max_adverse_markout_ticks"])
        labelled.append((row["seq_id"], tgt))
    return labelled


def test_THE_CAUSE_slow_fills_could_never_be_labelled_at_all():
    """A rung admitted at t=0 that fills 40s later is invisible to the old
    join, because 40 > 12. A rung that fills in one cycle is always caught."""
    ds = PF.ToxicityDataset()
    fast_t, slow_t = 0.0 + CYCLE_S, 0.0 + 40.0
    tox_row(ds, t=fast_t)
    tox_row(ds, t=slow_t)
    fills = [fill_row(seq_id=1, t_candidate=0.0, first_fill_t=fast_t,
                      markout_60s=-5.0),
             fill_row(seq_id=2, t_candidate=0.0, first_fill_t=slow_t,
                      markout_60s=+30.0)]
    _legacy_join(ds.rows, fills)
    got = [(r["t"], (r["outcome"] or {}).get("markout_60s_ticks"))
           for r in ds.rows]
    # the FAST fill is labelled; the SLOW one is silently dropped
    assert got[0][1] is not None
    assert ds.rows[1]["outcome"] is None
    # and both fills carried the SAME t_candidate, so the surviving label is
    # whichever capture happened to sit nearest -- not the matching fill
    assert ds.rows[0]["outcome"]["markout_60s_ticks"] in (-5.0, 30.0)


def test_THE_BIAS_the_legacy_subsample_is_enriched_for_TOXIC_fills():
    """Fast fills are adverse; slow fills are benign. Labelling only the fast
    ones reports a toxic share far above the cohort's own."""
    ds = PF.ToxicityDataset()
    fills = []
    for i in range(10):
        fast = i < 5
        cand = 1000.0 * i
        ft = cand + (CYCLE_S if fast else 45.0)
        tox_row(ds, t=ft, symbol="S%d" % i)
        fills.append(fill_row(seq_id=i, t_candidate=cand, first_fill_t=ft,
                              markout_60s=(-20.0 if fast else +20.0),
                              symbol="S%d" % i))
    for i in range(10):
        _legacy_join([r for r in ds.rows if r["symbol"] == "S%d" % i],
                     [f for f in fills if f["symbol"] == "S%d" % i],
                     symbol="S%d" % i)
    lab = ds.labelled()
    assert len(lab) == 5                       # only the fast half survives
    assert all(r["outcome"]["toxic"] for r in lab)
    # the TRUE cohort is 50% toxic; the labelled subsample reports 100%
    true_toxic = sum(1 for f in fills
                     if f["markout_ticks_by_horizon_s"]["60"] < 0) / len(fills)
    assert true_toxic == 0.5
    assert ds.report()["by_leg"]["YES"]["toxic_share"] == 1.0


def test_THE_MISATTRIBUTION_one_fills_outcome_could_land_on_anothers_features():
    ds = PF.ToxicityDataset()
    a = tox_row(ds, t=100.0, leg="YES", price="0.40")
    fills = [fill_row(seq_id=7, t_candidate=95.0, first_fill_t=250.0,
                      markout_60s=-99.0)]
    _legacy_join(ds.rows, fills)
    # the capture at t=100 belongs to a fill at t=100, not the one at t=250
    assert a["outcome"] is not None
    assert a["outcome"]["markout_60s_ticks"] == -99.0
    assert abs(a["t"] - fills[0]["first_fill_t"]) == 150.0


# ------------------------------------------------------------- the fix
def _exact_join(ds, fill_rows, symbol="S"):
    """The corrected join: same clock, same event, exact match, one capture
    per fill, no fallback."""
    byt = {}
    for r in ds.rows:
        if r.get("outcome") is not None or r.get("symbol") != symbol:
            continue
        byt.setdefault(round(float(r["t"]), 3), []).append(r)
    for row in fill_rows:
        if row.get("symbol") != symbol or not row.get("first_fill"):
            continue
        ft = row.get("first_fill_t")
        if ft is None:
            continue
        bucket = byt.get(round(float(ft), 3))
        if not bucket:
            continue
        PF.ToxicityDataset.label(
            bucket.pop(0),
            markout_60s=row["markout_ticks_by_horizon_s"].get("60"),
            complement_filled=row["complement_filled"],
            max_adverse_ticks=row["max_adverse_markout_ticks"],
            fill_seq_id=row["seq_id"],
            join_method=PF.ToxicityDataset.JOIN_FILL_T)


def test_THE_FIX_labels_every_fill_regardless_of_how_long_it_took():
    ds = PF.ToxicityDataset()
    fast_t, slow_t = 8.0, 40.0
    tox_row(ds, t=fast_t)
    tox_row(ds, t=slow_t)
    fills = [fill_row(seq_id=1, t_candidate=0.0, first_fill_t=fast_t,
                      markout_60s=-5.0),
             fill_row(seq_id=2, t_candidate=0.0, first_fill_t=slow_t,
                      markout_60s=+30.0)]
    _exact_join(ds, fills)
    assert [r["outcome"]["markout_60s_ticks"] for r in ds.rows] == [-5.0, 30.0]
    assert [r["outcome"]["fill_seq_id"] for r in ds.rows] == [1, 2]
    assert ds.report()["by_leg"]["YES"]["toxic_share"] == 0.5


def test_THE_FIX_two_fills_in_one_cycle_do_not_overwrite_each_other():
    ds = PF.ToxicityDataset()
    tox_row(ds, t=8.0, leg="YES")
    tox_row(ds, t=8.0, leg="NO")
    fills = [fill_row(seq_id=1, t_candidate=0.0, first_fill_t=8.0,
                      markout_60s=-5.0),
             fill_row(seq_id=2, t_candidate=0.0, first_fill_t=8.0,
                      markout_60s=+7.0, leg="NO")]
    _exact_join(ds, fills)
    assert sorted(r["outcome"]["fill_seq_id"] for r in ds.rows) == [1, 2]


def test_THE_FIX_refuses_to_label_a_fill_it_cannot_join():
    ds = PF.ToxicityDataset()
    tox_row(ds, t=8.0)
    _exact_join(ds, [fill_row(seq_id=1, t_candidate=0.0, first_fill_t=9.5,
                              markout_60s=-5.0)])
    assert ds.rows[0]["outcome"] is None       # unlabelled, not nearest-matched


# -------------------------------------------------------- the loud check
def test_the_reconciliation_AGREES_because_the_label_is_a_COPY():
    """There is ONE markout computation. The check exists to assert that
    invariant every window rather than assume it."""
    ds = PF.ToxicityDataset()
    tox_row(ds, t=8.0)
    fills = [fill_row(seq_id=1, t_candidate=0.0, first_fill_t=8.0,
                      markout_60s=-5.0)]
    _exact_join(ds, fills)
    rec = ds.reconcile(fills)
    assert rec["ok"] is True
    assert rec["checked"] == 1 and rec["agree"] == 1 and rec["disagree"] == 0
    assert rec["by_join_method"] == {PF.ToxicityDataset.JOIN_FILL_T: 1}


def test_the_reconciliation_FAILS_LOUDLY_if_the_stores_ever_diverge():
    ds = PF.ToxicityDataset()
    tox_row(ds, t=8.0)
    fills = [fill_row(seq_id=1, t_candidate=0.0, first_fill_t=8.0,
                      markout_60s=-5.0)]
    _exact_join(ds, fills)
    fills[0]["markout_ticks_by_horizon_s"]["60"] = +5.0      # forced divergence
    rec = ds.reconcile(fills)
    assert rec["ok"] is False
    assert rec["disagree"] == 1
    assert "void" in rec["why"]
    m = rec["mismatches"][0]
    assert m["toxicity_markout_60s"] == -5.0
    assert m["fill_sequence_markout_60s"] == +5.0


def test_legacy_rows_are_reported_as_UNJOINABLE_and_named_as_such():
    """Rows labelled before the fix carry no fill id and must never be pooled
    with clean ones."""
    ds = PF.ToxicityDataset()
    a = tox_row(ds, t=8.0)
    PF.ToxicityDataset.label(a, markout_60s=-3.0, complement_filled=False,
                             max_adverse_ticks=-3.0)          # legacy shape
    rec = ds.reconcile([])
    assert rec["unjoinable"] == 1
    assert rec["by_join_method"] == {PF.ToxicityDataset.JOIN_LEGACY: 1}
    assert rec["ok"] is True          # nothing DISAGREES; it is unjoinable


def test_there_is_exactly_one_markout_computation_in_the_system():
    """A sign inversion between the stores was never possible: the toxicity
    label COPIES the fill row's value rather than deriving one.

    Asserted over the parsed tree, not by scraping prose. `label` may compare
    (`markout_60s < 0`) but must perform no arithmetic: any Sub/Add/Mult/Div
    in its body would mean a second, independent markout computation had been
    introduced, and the two stores could then genuinely disagree.
    """
    import ast
    import inspect

    from src.gemini_execution.econ import arm as ARM

    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(PF.ToxicityDataset.label)))
    ops = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp)]
    assert ops == [], "label() now does arithmetic: %r" % (
        [ast.dump(o) for o in ops],)

    # and the value it stores comes from its own parameter, nothing else
    stores = [n for n in ast.walk(tree)
              if isinstance(n, ast.Name) and n.id == "markout_60s"]
    assert stores, "label() no longer reads the markout it is given"

    # the arm hands it the fill row's own field and computes nothing either
    fin = ast.parse(textwrap.dedent(
        inspect.getsource(ARM.GeminiEconArm.finish)))
    calls = [n for n in ast.walk(fin)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "label"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "markout_60s" in kw
    assert not isinstance(kw["markout_60s"], ast.BinOp)


# ------------------------------------------- what the 60s number measures
def test_the_markout_records_the_sample_it_ACTUALLY_used_not_the_horizon():
    """A 60s markout is taken at the first cycle at or AFTER 60s. At an 8s
    cadence that lands anywhere in [60, 68), and nothing recorded which. The
    realised age and its error now travel with the number."""
    from decimal import Decimal as D

    from src.gemini_execution.econ import fill_sequence as FS

    led = FS.FillSequenceLedger(arm="PP_F0")
    sq = led.candidate(cohort="btc5m", symbol="S", t=0.0, tte_s=250,
                       leg="YES", price="0.40")
    sq.note_rest(["YES"])
    sq.note_fill({"leg": "YES", "price": "0.40", "qty": "1"}, t=T0)
    book = {"yes": {"bid": "0.37", "ask": "0.45"},
            "no": {"bid": "0.55", "ask": "0.63"}}
    sq.observe(book=book, t=T0 + 65.0)            # first cycle at or after 60s

    m = sq.markout_meta["60"]
    assert m["requested_age_s"] == 60.0
    assert m["realised_age_s"] == 65.0
    assert m["age_error_s"] == 5.0
    assert m["ref_t"] == T0 + 65.0
    assert m["ref_field"] == "held_leg_bid"
    assert m["ref_price"] == "0.37"
    assert m["entry_price"] == "0.40"
    # (0.37 - 0.40) / 0.01 = -3 ticks, negative = adverse to a long YES maker
    assert sq.markouts["60"] == pytest.approx(-3.0)
    assert "POSITIVE is favourable" in m["formula"]


def test_ALIASING_one_late_reading_fills_every_horizon_with_the_same_number():
    """THE DEFECT THIS EXPOSES. If the book is unreadable for two minutes the
    first reading afterwards satisfies 8s, 30s, 60s and 120s at once, so the
    four horizons are NOT independent measurements and any claim that markout
    'deteriorates with horizon' is void on such a row."""
    from src.gemini_execution.econ import fill_sequence as FS

    led = FS.FillSequenceLedger(arm="PP_F0")
    sq = led.candidate(cohort="btc5m", symbol="S", t=0.0, tte_s=250,
                       leg="YES", price="0.40")
    sq.note_rest(["YES"])
    sq.note_fill({"leg": "YES", "price": "0.40", "qty": "1"}, t=T0)
    sq.observe(book={"yes": {"bid": "0.30", "ask": "0.45"},
                     "no": {"bid": "0.55", "ask": "0.70"}}, t=T0 + 130.0)

    assert set(sq.markouts) == {"8", "30", "60", "120"}
    assert len(set(sq.markouts.values())) == 1          # one number, four keys
    assert sorted(sq.markout_meta["60"]["aliased_with"]) == ["120", "30", "8"]
    assert sq.markout_meta["8"]["age_error_s"] == 122.0


def test_a_normally_sampled_row_is_NOT_flagged_as_aliased():
    from src.gemini_execution.econ import fill_sequence as FS

    led = FS.FillSequenceLedger(arm="PP_F0")
    sq = led.candidate(cohort="btc5m", symbol="S", t=0.0, tte_s=250,
                       leg="YES", price="0.40")
    sq.note_rest(["YES"])
    sq.note_fill({"leg": "YES", "price": "0.40", "qty": "1"}, t=T0)
    for dt, bid in ((9.0, "0.39"), (33.0, "0.38"), (62.0, "0.37"),
                    (121.0, "0.36")):
        sq.observe(book={"yes": {"bid": bid, "ask": "0.45"},
                         "no": {"bid": "0.55", "ask": "0.61"}}, t=T0 + dt)
    assert [sq.markouts[k] for k in ("8", "30", "60", "120")] == \
        pytest.approx([-1.0, -2.0, -3.0, -4.0])
    for k in ("8", "30", "60", "120"):
        assert "aliased_with" not in sq.markout_meta[k]


# ------------------------------------------------------ the row-level table
def test_the_row_table_carries_every_field_the_audit_asked_for():
    from src.gemini_execution.econ import fill_sequence as FS

    led = FS.FillSequenceLedger(arm="PP_F0")
    sq = led.candidate(cohort="btc5m", symbol="S", t=0.0, tte_s=250,
                       leg="YES", price="0.40")
    sq.note_rest(["YES"])
    sq.note_fill({"leg": "YES", "price": "0.40", "qty": "1"}, t=T0 + 100.0)
    sq.observe(book={"yes": {"bid": "0.37", "ask": "0.45"},
                     "no": {"bid": "0.55", "ask": "0.63"}}, t=T0 + 165.0)
    led.close_symbol("S", residual_qty="1")
    frow = led.rows[0]

    ds = PF.ToxicityDataset()
    tox_row(ds, t=T0 + 100.0, leg="YES", price="0.40")
    _exact_join(ds, [frow])

    tbl = ds.reconciliation_rows([frow])
    assert len(tbl) == 1
    r = tbl[0]
    for k in ("fill_seq_id", "observation_id", "symbol", "leg_fill_sequence",
              "leg_toxicity", "fill_t_fill_sequence", "fill_t_toxicity",
              "fill_price_fill_sequence", "quantity",
              "comparison_t_selected", "comparison_realised_age_s",
              "comparison_age_error_s", "reference_field_fill_sequence",
              "reference_price_fill_sequence", "markout_60s_fill_sequence",
              "markout_60s_toxicity", "toxicity_label", "join_method",
              "formula_fill_sequence", "formula_toxicity"):
        assert k in r, k
    assert r["leg_agrees"] is True
    assert r["fill_t_agrees"] is True
    assert r["markouts_agree"] is True
    assert r["comparison_age_error_s"] == 5.0
    assert r["markout_60s_fill_sequence"] == pytest.approx(-3.0)
    assert r["toxicity_label"] is True
    assert "COPY" in r["reference_field_toxicity"]


def test_a_LEGACY_row_never_appears_in_the_row_table():
    """It has no fill id, so it cannot honestly be placed beside a fill."""
    ds = PF.ToxicityDataset()
    a = tox_row(ds, t=8.0)
    PF.ToxicityDataset.label(a, markout_60s=-3.0, complement_filled=False,
                             max_adverse_ticks=-3.0)
    assert ds.reconciliation_rows([fill_row(seq_id=1, t_candidate=0.0,
                                            first_fill_t=8.0,
                                            markout_60s=-3.0)]) == []
