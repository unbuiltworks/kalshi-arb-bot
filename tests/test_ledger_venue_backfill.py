"""When the venue executed shares our ledger never booked, fix the ledger.

LIVE 2026-09-12T01:24:47Z. Everything else in the continuation preflight
passed -- venue_settled ok with all 93 markets settled, a clean 32-page walk
that ended `older_than_since`, zero audit_unavailable, positions flat, no
open orders, buying power $85.34, rung R5FT sized. One check failed:

    ledger_reconciled: skaser-abaspa  venue 17.1800  ledger 16.20
                       vervas-kohboh  venue 13.8200  ledger  9.48

Venue HIGHER both times, on markets that are venue-confirmed SETTLED and
REALIZED with the account flat. The venue executed fills that no session
ever wrote down -- the same class as the cs2 case at 15:46Z. The venue's
trade activities are authoritative and our file is the one that is wrong,
so the repair belongs in the file. The check itself stays exactly as strict.
"""
import json

import pytest

from src import pmus_settlement_auditor as auditor


def _trade(tid, qty, price="0.5000"):
    return {"id": tid, "qty_decimal_raw": qty, "price": price,
            "createTime": "2026-09-08T12:00:00Z"}


def _audit(**over):
    out = {"market": "aec-setkameua-vervas-kohboh-2026-09-08",
           "settled": True, "venue_qty_total": "13.8200",
           "ledger_qty_total": "9.48",
           "trade_summary": {"trades": [_trade("t1", "9.48"),
                                        _trade("t2", "4.34")]}}
    out.update(over)
    return out


@pytest.fixture
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("PMUS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(auditor.q1, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(auditor.q1, "ledger_append", lambda row: (
        (tmp_path / "ledger.jsonl").open("a").write(
            json.dumps({"t": "2026-09-12T01:30:00Z", **row}) + "\n")))
    return tmp_path


def _rows(path):
    p = path / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ------------------------------------------------------------- the repair
def test_missing_venue_fills_are_booked_with_their_trade_ids(_ledger):
    out = auditor.backfill_missing_venue_fills(_audit())
    assert out["backfilled"] == ["t1", "t2"]
    rows = _rows(_ledger)
    assert [r["venue_trade_id"] for r in rows] == ["t1", "t2"]
    assert all(r["kind"] == "filled" for r in rows)
    assert all(r["source"] == auditor.BACKFILL_SOURCE for r in rows), (
        "provenance: never confusable with a live fill event")
    assert all(r["slug"].endswith("vervas-kohboh-2026-09-08") for r in rows)


def test_the_repair_is_idempotent_across_preflight_passes(_ledger):
    """The waiting loop re-audits every pass. Writing again each time would
    inflate the ledger past the venue and invert the very divergence this
    exists to close."""
    auditor.backfill_missing_venue_fills(_audit())
    second = auditor.backfill_missing_venue_fills(_audit())
    assert second["backfilled"] == []
    assert "already booked" in second["skipped"]
    assert len(_rows(_ledger)) == 2, "still two rows, not four"


# -------------------------------------------------- the three refusals
def test_it_refuses_on_a_market_that_is_not_venue_confirmed_settled(_ledger):
    """An open market may have fills in flight; writing here would race the
    session that owns them."""
    out = auditor.backfill_missing_venue_fills(_audit(settled=False))
    assert out["backfilled"] == [] and "not venue-confirmed settled" in out["skipped"]
    assert _rows(_ledger) == []


def test_it_refuses_when_the_ledger_exceeds_the_venue(_ledger):
    """The dangerous direction. A ledger row the venue does not show is a
    worse problem than a missing one and must keep blocking."""
    out = auditor.backfill_missing_venue_fills(
        _audit(venue_qty_total="9.48", ledger_qty_total="13.82"))
    assert out["backfilled"] == []
    assert "never repaired here" in out["skipped"]
    assert _rows(_ledger) == []


def test_it_refuses_when_either_quantity_is_unknown(_ledger):
    for over in ({"venue_qty_total": None}, {"ledger_qty_total": None}):
        out = auditor.backfill_missing_venue_fills(_audit(**over))
        assert out["backfilled"] == [] and "unknown" in out["skipped"]
    assert _rows(_ledger) == []


def test_an_exact_match_writes_nothing(_ledger):
    out = auditor.backfill_missing_venue_fills(
        _audit(venue_qty_total="9.48", ledger_qty_total="9.48"))
    assert out["backfilled"] == []
    assert _rows(_ledger) == []


# ------------------------------------------------- through the preflight
def test_the_preflight_repairs_then_judges_the_corrected_ledger(monkeypatch):
    """The whole point: the reconcile check must see the fixed numbers, not
    the ones that provoked the fix."""
    from src import pmus_l5_continuous_session as cont
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.setattr(cont, "_preflight_sleep", lambda s: None)
    monkeypatch.setattr(cont, "settle_held", lambda t, s, walk=None: {
        "status": "REALIZED", "settled": True, "market": s,
        "settled_by": "position_resolution_activity",
        "realized_confirmed": True, "realized_usd": "-1.5778",
        "venue_qty_total": "13.8200", "ledger_qty_total": "9.48"})
    monkeypatch.setattr(cont, "_backfill_venue_fills",
                        lambda audit: {"market": audit["market"],
                                       "backfilled": ["t2"], "qty": "4.34"})
    monkeypatch.setattr(cont, "_ledger_qty", lambda slug: "13.8200")
    pre = cont.continuation_preflight(None, {"m"}, {"m"})
    vs = pre["checks"]["venue_settled"]
    assert vs["markets"]["m"]["ledger_qty_total"] == "13.8200"
    assert vs["markets"]["m"]["ledger_backfill"]["backfilled"] == ["t2"]
    assert vs["ledger_vs_venue_qty_mismatch"] == []
    assert pre["ok"] is True


def test_a_divergence_the_repair_cannot_close_still_blocks(monkeypatch):
    """Ledger above venue: refused by the repair, so it must still fail.

    Passed a SIZED rung, because ledger_reconciled has always been blocking
    only for one (OWNER 5561465636 #2) -- the canary runs R5FT, which is."""
    from src import pmus_l5_continuous_session as cont
    from src import pmus_l5_ramp_ladder as ladder
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.setattr(cont, "_preflight_sleep", lambda s: None)
    monkeypatch.setattr(cont, "settle_held", lambda t, s, walk=None: {
        "status": "REALIZED", "settled": True, "market": s,
        "settled_by": "position_resolution_activity",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "9.48", "ledger_qty_total": "13.8200"})
    monkeypatch.setattr(cont, "_backfill_venue_fills",
                        lambda audit: {"market": audit["market"],
                                       "backfilled": [],
                                       "skipped": "never repaired here"})
    pre = cont.continuation_preflight(None, {"m"}, {"m"},
                                      rung=ladder.rung("R5FT"))
    assert pre["checks"]["venue_settled"]["ledger_vs_venue_qty_mismatch"] == ["m"]
    assert pre["checks"]["ledger_reconciled"]["ok"] is False
    assert pre["checks"]["ledger_reconciled"]["blocking"] is True
    assert pre["ok"] is False and "ledger_reconciled" in pre["failed"]


def test_a_failing_repair_never_takes_the_preflight_down(monkeypatch):
    from src import pmus_l5_continuous_session as cont
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.setattr(cont, "_preflight_sleep", lambda s: None)
    monkeypatch.setattr(cont, "settle_held", lambda t, s, walk=None: {
        "status": "REALIZED", "settled": True, "market": s,
        "settled_by": "position_resolution_activity",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "1", "ledger_qty_total": "1"})
    def _boom(audit):
        raise RuntimeError("boom")
    monkeypatch.setattr(cont, "_backfill_venue_fills", _boom)
    pre = cont.continuation_preflight(None, {"m"}, {"m"})
    assert pre["ok"] is True
    assert "boom" in pre["checks"]["venue_settled"]["markets"]["m"]["ledger_backfill"]["error"]
