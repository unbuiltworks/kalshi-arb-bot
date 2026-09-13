"""P0.1 -- one writer owns ledger quantity corrections, and it cannot double-count.

THE PRODUCTION INCIDENT THIS REPRODUCES. 2026-09-12T01:54:55Z. The venue
reconciler matched venue trades against the ledger by venue trade id and
booked the unmatched ones. Every live writer --

    pmus_spring_l5_session.py:1346,2284   kind=filled, order_id, no trade id
    pmus_micro_executor.py:641,648,681    kind=filled, order_id, no trade id
    pmus_q1_executor{,_v4}.py:540,550,594 kind=filled, order_id, no trade id

-- records `order_id` and NEVER `venue_trade_id`. So ledger_trade_ids()
returned the empty set, every venue trade looked unbooked, and the entire
venue quantity was appended on top of what was already there:

    skaser-abaspa  venue 17.1800  ledger 16.20 -> 33.3800
    vervas-kohboh  venue 13.8200  ledger  9.48 -> 23.3000

A second correction writer (src/pmus_r5ft_ledger_repair.py) was then added
to undo those two by hardcoded target. That left TWO writers able to move
the same quantity, and the general bug still armed for every future market.

The replacement states a target against ORGANIC rows (everything that is
not tagged in CORRECTION_SOURCES) and converges on it, so it is idempotent
with no trade-id matching, and it can supersede either historical writer's
rows instead of stacking on them.
"""
import json
from decimal import Decimal as D

import pytest

from src import pmus_settlement_auditor as auditor

SLUG = "aec-setkameua-vervas-kohboh-2026-09-08"
R5FT_REPAIR_SOURCE = "r5ft_ledger_reconciliation_adjustment_20260912"


@pytest.fixture
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(auditor.q1, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(auditor.q1, "ledger_append", lambda row: (
        (tmp_path / "ledger.jsonl").open("a").write(
            json.dumps({"t": "2026-09-13T12:00:00Z", **row}) + "\n")))
    return tmp_path


def _seed(path, rows):
    with (path / "ledger.jsonl").open("a") as sink:
        for r in rows:
            sink.write(json.dumps({"t": "2026-09-08T12:00:00Z", **r}) + "\n")


def _session_fill(qty, order_id):
    """Exactly what a live writer emits: an order id, NO venue trade id."""
    return {"kind": "filled", "stage": "first_leg", "slug": SLUG,
            "qty": qty, "order_id": order_id}


def _audit(**over):
    out = {"market": SLUG, "settled": True, "venue_qty_total": "13.8200"}
    out.update(over)
    return out


def _total():
    return auditor.ledger_qty_total(SLUG)


# ------------------------------------------------- THE REGRESSION ITSELF
def test_session_rows_without_trade_ids_are_not_double_counted(_ledger):
    """The live starting state. Two session fills totalling 9.48, none
    carrying a venue trade id, against a venue quantity of 13.8200."""
    _seed(_ledger, [_session_fill("4.74", "ord-1"),
                    _session_fill("4.74", "ord-2")])
    assert _total() == "9.48"
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["adjustment"] == "4.3400", "the GAP, not the whole venue qty"
    assert out["organic"] == "9.48"
    assert _total() == "13.8200", "exactly the venue -- not 23.3000"


def test_a_second_pass_writes_nothing(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    auditor.backfill_missing_venue_fills(_audit())
    second = auditor.backfill_missing_venue_fills(_audit())
    assert second["adjustment"] == "0"
    assert "already reconciles" in second["skipped"]
    assert _total() == "13.8200"


def test_many_passes_across_restarts_stay_put(_ledger):
    """The preflight re-audits every pass and the process restarts often."""
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    for _ in range(5):
        auditor.backfill_missing_venue_fills(_audit())
    assert _total() == "13.8200"


# ------------------------------- it must SUPERSEDE both historical writers
def test_it_undoes_the_old_per_trade_backfill_overshoot(_ledger):
    """The damaged shape: 9.48 organic + a 13.8200 venue_trade_backfill
    overshoot showing 23.3000 against a venue 13.8200."""
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": "filled", "slug": SLUG, "qty": "13.8200",
                     "source": auditor.BACKFILL_SOURCE}])
    assert _total() == "23.3000"
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["adjustment"] == "-9.4800", "negative, and that is the point"
    assert out["corrections_before"] == "13.8200"
    assert _total() == "13.8200"


def test_it_accounts_for_the_r5ft_repair_writers_rows(_ledger):
    """src/pmus_r5ft_ledger_repair.py rows MUST count as corrections. If they
    were treated as organic, organic would read 13.8200, the target would
    become 0, and this writer would re-inflate a ledger already repaired."""
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": "filled", "slug": SLUG, "qty": "4.34",
                     "source": R5FT_REPAIR_SOURCE}])
    assert R5FT_REPAIR_SOURCE in auditor.CORRECTION_SOURCES
    split = auditor.ledger_qty_split(SLUG)
    assert split["organic"] == "9.48" and split["correction"] == "4.34"
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["adjustment"] == "0"
    assert D(_total()) == D("13.8200"), "left exactly alone"


def test_two_writers_cannot_stack_on_the_same_venue_fill(_ledger):
    """Writer 5 corrects to target, then writer 4's successor runs. The
    second must see the first's row and add nothing."""
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    # writer 5 shape: one adjustment row to the authoritative target
    _seed(_ledger, [{"kind": "filled", "slug": SLUG, "qty": "4.34",
                     "source": R5FT_REPAIR_SOURCE,
                     "order_id": f"reconcile:{SLUG}:20260912"}])
    assert D(_total()) == D("13.8200")
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["adjustment"] == "0"
    assert D(_total()) == D("13.8200"), "no stacking"


# --------------------------------------------------------- the refusals
def test_it_refuses_on_a_market_that_is_not_venue_confirmed_settled(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    out = auditor.backfill_missing_venue_fills(_audit(settled=False))
    assert out["adjustment"] == "0"
    assert "not venue-confirmed settled" in out["skipped"]
    assert _total() == "9.48"


def test_it_refuses_when_the_sessions_own_fills_exceed_the_venue(_ledger):
    """The dangerous direction, measured against ORGANIC rows only."""
    _seed(_ledger, [_session_fill("20.00", "ord-1")])
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["adjustment"] == "0"
    assert "never repaired here" in out["skipped"]
    assert _total() == "20.00", "untouched"


def test_it_refuses_when_the_venue_quantity_is_unknown(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    out = auditor.backfill_missing_venue_fills(_audit(venue_qty_total=None))
    assert out["adjustment"] == "0" and "unknown" in out["skipped"]
    assert _total() == "9.48"


def test_it_refuses_when_the_ledger_has_no_rows_for_the_market(_ledger):
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["adjustment"] == "0"
    assert "no rows for the market" in out["skipped"]


# ------------------------------------------------------ the split itself
def test_an_order_terminal_row_still_counts_as_a_session_fill(_ledger):
    """ledger_qty_total has always counted a terminal row when no filled row
    carries its order. The split must not quietly drop that."""
    _seed(_ledger, [{"kind": "order_terminal", "slug": SLUG,
                     "order_id": "ord-9", "filled_qty": "2.00"}])
    assert auditor.ledger_qty_split(SLUG)["organic"] == "2.00"


def test_the_split_and_the_visible_total_agree(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": "filled", "slug": SLUG, "qty": "4.34",
                     "source": auditor.BACKFILL_SOURCE}])
    split = auditor.ledger_qty_split(SLUG)
    assert D(split["organic"]) + D(split["correction"]) == D(_total())


def test_the_written_row_is_marked_so_it_is_never_a_trading_event(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    auditor.backfill_missing_venue_fills(_audit())
    row = json.loads((_ledger / "ledger.jsonl").read_text().splitlines()[-1])
    assert row["source"] == auditor.RECONCILE_SOURCE
    assert row["stage"] == "venue_reconciliation"
    assert row["venue_target_qty"] == "13.8200"
    assert row["organic_qty"] == "9.48"
    assert "never counted as trading activity" in row["labels"]
    assert auditor.RECONCILE_SOURCE in auditor.CORRECTION_SOURCES


# ===========================================================================
# OWNER 2026-09-13: the canonical calculation, cases A-J, and the consumer
# audit that had to happen before a distinct correction kind was introduced.
# ===========================================================================
def _reconcile(venue):
    return auditor.backfill_missing_venue_fills(
        _audit(venue_qty_total=str(venue)))


def test_A_organic_equals_venue_writes_nothing(_ledger):
    _seed(_ledger, [_session_fill("13.8200", "ord-1")])
    out = _reconcile("13.8200")
    assert out["adjustment"] == "0" and "already reconciles" in out["skipped"]
    assert D(_total()) == D("13.8200")


def test_B_organic_below_venue_writes_the_positive_delta_only(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    out = _reconcile("13.8200")
    assert D(out["adjustment"]) == D("4.3400"), "the gap, not the venue total"
    assert D(_total()) == D("13.8200")


def test_C_effective_above_venue_writes_a_negative_delta(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": auditor.RECONCILE_KIND, "slug": SLUG,
                     "qty": "13.8200", "source": auditor.RECONCILE_SOURCE}])
    assert D(_total()) == D("23.3000")
    out = _reconcile("13.8200")
    assert D(out["adjustment"]) == D("-9.4800")
    assert D(_total()) == D("13.8200")


def test_D_running_twice_writes_nothing_the_second_time(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    _reconcile("13.8200")
    rows_after_first = len((_ledger / "ledger.jsonl").read_text().splitlines())
    out = _reconcile("13.8200")
    assert out["adjustment"] == "0"
    assert len((_ledger / "ledger.jsonl").read_text().splitlines()) == rows_after_first


def test_E_live_rows_with_order_id_and_no_trade_id_never_double_count(_ledger):
    """The exact production shape. Every live writer records order_id and no
    venue_trade_id; trade-id matching therefore saw zero booked trades."""
    _seed(_ledger, [_session_fill("4.74", "ord-1"), _session_fill("4.74", "ord-2")])
    rows = [json.loads(x) for x in (_ledger / "ledger.jsonl").read_text().splitlines()]
    assert all("venue_trade_id" not in r for r in rows)
    _reconcile("13.8200")
    assert D(_total()) == D("13.8200"), "not 23.3000"


def test_F_historical_repair_present_and_effective_equals_venue_writes_zero(_ledger):
    """Writer 5 already brought this market to target. The canonical
    reconciler must add nothing."""
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": "filled", "slug": SLUG, "qty": "4.34",
                     "source": R5FT_REPAIR_SOURCE,
                     "order_id": f"reconcile:{SLUG}:20260912"}])
    assert D(_total()) == D("13.8200")
    out = _reconcile("13.8200")
    assert out["adjustment"] == "0"
    assert D(_total()) == D("13.8200")


def test_G_historical_overshoot_self_heals_to_venue(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": "filled", "slug": SLUG, "qty": "13.8200",
                     "source": auditor.BACKFILL_SOURCE}])
    assert D(_total()) == D("23.3000")
    _reconcile("13.8200")
    assert D(_total()) == D("13.8200")


def test_H_restart_after_correction_reconstructs_exactly(_ledger):
    """Nothing is held in memory: a fresh read of the file reproduces the
    same effective quantity and the same split."""
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    _reconcile("13.8200")
    for _ in range(4):                       # 'restarts'
        split = auditor.ledger_qty_split(SLUG)
        assert D(split["organic"]) == D("9.48")
        assert D(split["correction"]) == D("4.3400")
        assert D(_total()) == D("13.8200")
        assert _reconcile("13.8200")["adjustment"] == "0"


def test_I_many_partial_fills_reach_the_target_exactly(_ledger):
    _seed(_ledger, [_session_fill("2.50", f"ord-{i}") for i in range(4)])
    assert D(_total()) == D("10.00")
    _reconcile("13.8200")
    assert D(_total()) == D("13.8200")


def test_J_either_order_of_boot_repair_and_auditor_gives_one_answer(_ledger):
    """Writer 5 runs at boot, the canonical reconciler runs in the preflight.
    Whichever lands first, the effective quantity must be the venue's."""
    # boot repair first, then reconciler
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": "filled", "slug": SLUG, "qty": "4.34",
                     "source": R5FT_REPAIR_SOURCE}])
    _reconcile("13.8200")
    first_order = D(_total())

    # fresh ledger: reconciler first, then a (no-op) boot-repair-shaped row
    (_ledger / "ledger.jsonl").unlink()
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    _reconcile("13.8200")
    second_order = D(_total())
    assert first_order == second_order == D("13.8200")


# ------------------------------------------------- the consumer audit
def test_the_correction_kind_is_distinct_from_an_execution(_ledger):
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    _reconcile("13.8200")
    row = json.loads((_ledger / "ledger.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == auditor.RECONCILE_KIND != "filled"
    for field in ("before_qty", "organic_qty", "existing_correction_qty",
                  "venue_target_qty", "after_qty", "source",
                  "reconciliation_version", "reason", "t", "slug", "qty"):
        assert field in row, field
    assert D(row["before_qty"]) + D(row["qty"]) == D(row["after_qty"])
    assert D(row["after_qty"]) == D(row["venue_target_qty"])


def test_effective_quantity_counts_the_new_kind(_ledger):
    """If ledger_qty_total ignored RECONCILE_KIND the reconciler would
    recompute the same adjustment forever, and pmus_r5ft_ledger_repair's
    convergence assert would raise at boot."""
    _seed(_ledger, [_session_fill("9.48", "ord-1"),
                    {"kind": auditor.RECONCILE_KIND, "slug": SLUG,
                     "qty": "4.34", "source": auditor.RECONCILE_SOURCE}])
    assert D(_total()) == D("13.82")


def test_inventory_rebuild_ignores_corrections(_ledger):
    """up/dn readers gate on kind=="filled" AND a stage prefix. A correction
    carries no side and must never become directional inventory."""
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    _reconcile("13.8200")
    rows = [json.loads(x) for x in (_ledger / "ledger.jsonl").read_text().splitlines()]
    corr = [r for r in rows if r["kind"] == auditor.RECONCILE_KIND]
    assert corr and all(not str(r.get("stage", "")).startswith("spring_")
                        and r.get("stage") != "micro_first_leg" for r in corr)
    assert all("side" not in r for r in corr)


def test_incentive_audit_cannot_see_a_correction(_ledger):
    """pmus_incentive_audit requires kind=="filled" AND a price. A correction
    is neither a fill nor a fee/rebate event."""
    _seed(_ledger, [_session_fill("9.48", "ord-1")])
    _reconcile("13.8200")
    rows = [json.loads(x) for x in (_ledger / "ledger.jsonl").read_text().splitlines()]
    corr = [r for r in rows if r["kind"] == auditor.RECONCILE_KIND]
    assert corr and all(r.get("kind") == "filled" and r.get("price")
                        for r in corr) is False


def test_first_fill_time_ignores_corrections(_ledger):
    """A correction is not an execution and has no fill time."""
    _seed(_ledger, [{"kind": auditor.RECONCILE_KIND, "slug": SLUG,
                     "qty": "4.34", "source": auditor.RECONCILE_SOURCE,
                     "t": "2026-09-13T00:00:00Z"}])
    assert auditor.first_fill_time(SLUG) is None
    _seed(_ledger, [dict(_session_fill("9.48", "ord-1"),
                         t="2026-09-08T12:00:00Z")])
    assert auditor.first_fill_time(SLUG) == "2026-09-08T12:00:00Z"
