from decimal import Decimal
import json

from src.cross_venue_ledger import (
    LedgerSidecar,
    make_event,
    reconcile_events,
    summarize_ledger,
)

D = Decimal


def fill(group: str, venue: str, side: str, qty: str, price: str, *, external_id: str) -> object:
    return make_event(
        trade_group_id=group,
        settlement_key="mlb:2026-08-16:wsh:nym:lindor:hits:2",
        category="baseball_player_hits",
        venue=venue,
        market_id=f"{venue}-market",
        event_type="fill",
        side=side,
        quantity=D(qty),
        price=D(price),
        external_id=external_id,
        ts_ms=1,
    )


def settle(group: str, venue: str, payout: str, external_id: str, ts_ms: int = 3) -> object:
    return make_event(
        trade_group_id=group,
        settlement_key="mlb:2026-08-16:wsh:nym:lindor:hits:2",
        category="baseball_player_hits",
        venue=venue,
        market_id=f"{venue}-market",
        event_type="settlement",
        payout=D(payout),
        external_id=external_id,
        ts_ms=ts_ms,
    )


def test_emit_does_not_write_until_sidecar_drains(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    sidecar = LedgerSidecar(path)
    sidecar.emit(fill("g1", "polymarket_us", "YES", "1", "0.45", external_id="p1"))
    assert not path.exists()
    assert sidecar.drain_once() == 1
    row = json.loads(path.read_text().strip())
    assert row["trade_group_id"] == "g1"
    assert row["quantity"] == "1"


def test_reconcile_is_idempotent_by_venue_external_id() -> None:
    a = fill("g1", "kalshi", "NO", "1", "0.50", external_id="k-fill-1")
    duplicate = fill("g1", "kalshi", "NO", "1", "0.50", external_id="k-fill-1")
    merged = reconcile_events([a], [duplicate])
    assert len(merged) == 1


def test_summary_reports_locked_pending_and_unmatched_exposure() -> None:
    events = [
        fill("matched", "polymarket_us", "YES", "2", "0.45", external_id="p1"),
        fill("matched", "kalshi", "NO", "2", "0.50", external_id="k1"),
        make_event(
            trade_group_id="matched",
            settlement_key="mlb:2026-08-16:wsh:nym:lindor:hits:2",
            category="baseball_player_hits",
            venue="kalshi",
            market_id="k-market",
            event_type="fee",
            fee=D("0.02"),
            external_id="k-fee-1",
            ts_ms=2,
        ),
        fill("legged", "polymarket_us", "YES", "1", "0.40", external_id="p2"),
    ]
    result = summarize_ledger(events)
    assert result["pending_settlement_groups"] == 2
    assert result["partial_settlement_groups"] == 0
    assert result["unmatched_leg_exposure_contracts"] == "1"
    # matched group: $2 locked payout - $1.90 fills - $0.02 fee = $0.08
    # legged group contributes no locked payout and carries its $0.40 cost.
    assert result["locked_pnl_before_basis_risk"] == "-0.32"
    assert result["trade_groups"]["matched"]["matched_quantity"] == "2"
    assert result["trade_groups"]["legged"]["unmatched_leg_quantity"] == "1"


def test_partial_cross_venue_settlement_stays_pending_and_not_realized() -> None:
    events = [
        fill("g1", "polymarket_us", "YES", "1", "0.45", external_id="p1"),
        fill("g1", "kalshi", "NO", "1", "0.50", external_id="k1"),
        settle("g1", "polymarket_us", "1", "p-settle-1"),
    ]
    result = summarize_ledger(events)
    assert result["pending_settlement_groups"] == 1
    assert result["partial_settlement_groups"] == 1
    assert result["realized_pnl"] == "0"
    assert result["trade_groups"]["g1"]["partial_settlement"] is True
    assert result["trade_groups"]["g1"]["settled_venues"] == ["polymarket_us"]


def test_both_venue_settlements_replace_locked_with_realized_pnl() -> None:
    events = [
        fill("g1", "polymarket_us", "YES", "1", "0.45", external_id="p1"),
        fill("g1", "kalshi", "NO", "1", "0.50", external_id="k1"),
        settle("g1", "polymarket_us", "1", "p-settle-1"),
        settle("g1", "kalshi", "0", "k-settle-1", ts_ms=4),
    ]
    result = summarize_ledger(events)
    assert result["pending_settlement_groups"] == 0
    assert result["partial_settlement_groups"] == 0
    assert result["realized_pnl"] == "0.05"
    assert result["locked_pnl_before_basis_risk"] == "0"
    assert result["trade_groups"]["g1"]["partial_settlement"] is False
    assert result["trade_groups"]["g1"]["settled_venues"] == ["kalshi", "polymarket_us"]
