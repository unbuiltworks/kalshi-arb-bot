"""The OOS append guard: it must be incapable of re-testing the decision sample.

OWNER: "Don't reopen the rejected >$1 rescue decision on Sep1-10; only
genuinely unseen dates count."
"""
from __future__ import annotations

import datetime

import pytest

from src import l5_oos_append as A


def _ts(date: str, hour: int = 12) -> float:
    y, m, d = (int(x) for x in date.split("-"))
    return datetime.datetime(y, m, d, hour,
                             tzinfo=datetime.timezone.utc).timestamp()


def _row(date, **kw):
    r = {"timestamp": _ts(date), "type": "TRADE", "slug": "s", "size": "1"}
    r.update(kw)
    return r


# ------------------------------------------------- the refusal is the point
def test_a_decision_sample_date_is_REFUSED_not_filtered():
    """A silent filter would let a caller pass the whole dataset, watch a
    number come out, and never learn half of it was the decision sample."""
    with pytest.raises(A.SeenDateRefused) as e:
        A.assert_unseen(["2026-09-14", "2026-09-05"])
    assert "2026-09-05" in str(e.value)
    assert "2026-09-14" not in str(e.value), "only the offender is named"
    assert "manufactures significance" in str(e.value)


def test_every_date_of_the_decision_sample_is_refused():
    for d in A.DECISION_SAMPLE_DATES:
        with pytest.raises(A.SeenDateRefused):
            A.assert_unseen([d])
    assert len(A.DECISION_SAMPLE_DATES) == 10


def test_genuinely_unseen_dates_pass():
    A.assert_unseen(["2026-09-11", "2026-09-12", "2026-10-01"])


def test_unseen_dates_excludes_the_sample():
    rows = [_row("2026-09-05"), _row("2026-09-11"), _row("2026-09-12")]
    assert A.unseen_dates(rows) == ["2026-09-11", "2026-09-12"]


# ------------------------------------------------------------ append-only
def test_append_never_replaces_existing_rows():
    existing = [_row("2026-09-05", id="a")]
    out = A.append_rows(existing, [_row("2026-09-11", id="b")])
    assert out["existing"] == 1 and out["added"] == 1
    assert len(out["rows"]) == 2
    assert out["rows"][0]["id"] == "a", "existing row survives, unmodified"


def test_append_dedupes_by_venue_id():
    existing = [_row("2026-09-05", id="a")]
    out = A.append_rows(existing, [_row("2026-09-05", id="a"),
                                   _row("2026-09-11", id="b")])
    assert out["added"] == 1 and out["duplicates_dropped"] == 1


def test_rows_without_an_id_fall_back_to_venue_distinguishable_fields():
    existing = [_row("2026-09-05")]
    same = _row("2026-09-05")
    other = _row("2026-09-05", size="2")
    out = A.append_rows(existing, [same, other])
    assert out["added"] == 1, "only a genuinely different row is added"


def test_the_resume_point_is_the_latest_timestamp():
    rows = [_row("2026-09-05"), _row("2026-09-11")]
    assert A.utc_date(A.latest_timestamp(rows)) == "2026-09-11"
    assert A.latest_timestamp([]) is None


# ------------------------------------------------------- frozen protocol
def test_the_protocol_matches_the_decision_record():
    """Frozen before the OOS data existed; changing it is threshold
    shopping."""
    doc = open("analysis/l5/DECISIONS/"
               "2026-09-10_rescue_ceiling_REJECT.md").read()
    for c in A.FROZEN_CEILINGS:
        assert c in doc, c
    assert "1.000" in doc and "0.05" in doc
    p = A.protocol()
    assert p["ceilings"] == list(A.FROZEN_CEILINGS)
    assert p["alpha"] == 0.05
    assert p["unit_of_observation"] == "one UTC day"
    assert "l5_resolved_pnl" in p["accounting"]
    assert len(p["decision_sample_excluded"]) == 10


def test_no_extra_ceiling_can_creep_in():
    assert len(A.FROZEN_CEILINGS) == 6
    assert "1.01" not in A.FROZEN_CEILINGS, "1.010 exactly, not a re-spelling"


# ----------------------------------------------------------- readiness
def test_readiness_reports_rather_than_decides():
    rows = [_row("2026-09-11"), _row("2026-09-12")]
    r = A.readiness(rows, min_days=10)
    assert r["ready"] is False
    assert r["unseen_day_count"] == 2
    assert "only 2 unseen UTC day(s)" in r["why_not"]


def test_readiness_counts_DAYS_not_rows():
    """The frozen unit is one UTC day. A thousand rows on one day is n=1."""
    rows = [_row("2026-09-11", id=str(i)) for i in range(1000)]
    assert A.readiness(rows)["unseen_day_count"] == 1


def test_readiness_flags_the_decision_sample_it_can_see():
    rows = [_row("2026-09-05"), _row("2026-09-11")]
    r = A.readiness(rows)
    assert r["decision_sample_dates_present"] == ["2026-09-05"]


def test_the_canonical_dataset_today_has_zero_unseen_days():
    """Today the canonical harvest IS the decision sample, so the sweep must
    not be runnable at all."""
    rows = A.load_canonical().get("rows") or []
    r = A.readiness(rows)
    assert r["unseen_day_count"] == 0
    assert r["ready"] is False
    assert len(r["decision_sample_dates_present"]) == 10


# ---- unattended continuation ---------------------------------------------

def test_harvest_reports_zero_unseen_today_and_is_not_runnable():
    out = A.harvest_and_report()
    assert out["unseen_day_count"] == 0
    assert out["sweep_runnable"] is False
    assert set(out["decision_sample_excluded"]) == set(A.DECISION_SAMPLE_DATES)


def test_the_loop_never_auto_runs_the_ceiling_sweep():
    """Re-sweeping a growing sample every hour is threshold shopping with
    extra steps. Readiness is reported; the sweep waits for a deliberate
    call on a complete sample."""
    import ast
    tree = ast.parse(open("src/l5_oos_append.py").read())
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    run_body = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = {c.func.id for c in ast.walk(run_body)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert not any("sweep" in c for c in called), called
    assert "harvest_and_report" in called


def test_a_fetch_failure_is_reported_not_swallowed():
    def boom(_since):
        raise RuntimeError("venue down")
    out = A.harvest_and_report(fetch=boom)
    assert "venue down" in out["fetch_error"]
    assert out["rows_appended"] == 0


def test_a_fetch_returning_decision_sample_rows_is_refused():
    """The one thing this module exists to make impossible."""
    ts = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp()

    def seen(_since):
        return [{"timestamp": ts, "type": "TRADE", "slug": "x", "size": 1}]
    with pytest.raises(A.SeenDateRefused):
        A.assert_unseen(sorted(A.dates_present(seen(None))))


def test_run_emits_a_row_every_cycle_so_still_zero_is_visible(capsys):
    A.run(cycles=2, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert out.count(A.PREFIX) == 2
    assert '"sweep_runnable":false' in out.replace(" ", "")
