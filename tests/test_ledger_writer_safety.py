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
    assert row["venue_qty_total"] == "13.8200"
    assert row["ledger_organic_qty"] == "9.48"
    assert "never counted as trading activity" in row["labels"]
    assert auditor.RECONCILE_SOURCE in auditor.CORRECTION_SOURCES
