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
