from decimal import Decimal

from src.cross_venue_ledger import make_event, summarize_ledger
from src.mlb_prop_paper_settlement import reconcile_paper_ledger, settlement_events


def _fills(group="g1"):
    common = dict(
        trade_group_id=group,
        settlement_key="baseball_player_hits|2026-08-16|Example|2|poly-1|kalshi-1",
        category="mlb_prop_paper",
        ts_ms=100,
    )
    return [
        make_event(**common, venue="polymarket_us", market_id="poly-1", event_type="fill", side="YES", quantity=Decimal("2"), price=Decimal("0.45")),
        make_event(**common, venue="kalshi", market_id="kalshi-1", event_type="fill", side="NO", quantity=Decimal("2"), price=Decimal("0.51")),
        make_event(**common, venue="polymarket_us", market_id="poly-1", event_type="fee", fee=Decimal("0.01")),
        make_event(**common, venue="kalshi", market_id="kalshi-1", event_type="fee", fee=Decimal("0.02")),
    ]


def test_matching_observed_outcomes_create_expected_payouts():
    fills = _fills()
    settlements, audits = settlement_events(fills, [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "YES",
        "kalshi_outcome": "YES",
        "settled_ts_ms": 200,
    }])
    assert len(settlements) == 2
    by_venue = {event.venue: event for event in settlements}
    assert by_venue["polymarket_us"].payout == Decimal("2")
    assert by_venue["kalshi"].payout == Decimal("0")
    assert audits[0]["outcomes_agree"] is True
    summary = summarize_ledger(fills + settlements)
    assert Decimal(summary["realized_pnl"]) == Decimal("0.05")


def test_outcome_disagreement_is_preserved_not_forced_to_one_dollar_lock():
    fills = _fills()
    settlements, audits = settlement_events(fills, [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "NO",
        "kalshi_outcome": "YES",
        "settled_ts_ms": 200,
    }])
    assert len(settlements) == 2
    assert sum((event.payout for event in settlements), Decimal("0")) == Decimal("0")
    assert audits[0]["outcomes_agree"] is False
    summary = summarize_ledger(fills + settlements)
    assert Decimal(summary["realized_pnl"]) == Decimal("-1.95")


def test_partial_outcomes_fail_closed():
    settlements, audits = settlement_events(_fills(), [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "YES",
    }])
    assert settlements == []
    assert audits == []


def test_partial_outcome_row_does_not_suppress_later_complete_row():
    settlements, audits = settlement_events(_fills(), [
        {
            "trade_group_id": "g1",
            "polymarket_us_outcome": "YES",
        },
        {
            "trade_group_id": "g1",
            "polymarket_us_outcome": "YES",
            "kalshi_outcome": "YES",
            "settled_ts_ms": 200,
        },
    ])
    assert len(settlements) == 2
    assert len(audits) == 1
    assert audits[0]["trade_group_id"] == "g1"
    assert audits[0]["outcomes_agree"] is True


def test_identical_complete_duplicate_rows_are_idempotent():
    rows = [
        {"trade_group_id": "g1", "polymarket_us_outcome": "YES", "kalshi_outcome": "YES", "settled_ts_ms": 200},
        {"trade_group_id": "g1", "polymarket_us_outcome": "YES", "kalshi_outcome": "YES", "settled_ts_ms": 201},
    ]
    settlements, audits = settlement_events(_fills(), rows)
    assert len(settlements) == 2
    assert len(audits) == 1
    assert audits[0]["status"] == "SETTLED_FROM_OBSERVED_OUTCOMES"


def test_conflicting_complete_rows_fail_closed_instead_of_using_file_order():
    rows = [
        {"trade_group_id": "g1", "polymarket_us_outcome": "YES", "kalshi_outcome": "YES", "settled_ts_ms": 200},
        {"trade_group_id": "g1", "polymarket_us_outcome": "NO", "kalshi_outcome": "YES", "settled_ts_ms": 201},
    ]
    settlements, audits = settlement_events(_fills(), rows)
    assert settlements == []
    assert len(audits) == 1
    assert audits[0]["trade_group_id"] == "g1"
    assert audits[0]["status"] == "OUTCOME_CONFLICT_FAIL_CLOSED"
    assert audits[0]["settlement_events_created"] == 0


def test_existing_exact_leg_settlement_is_idempotent():
    fills = _fills()
    existing = make_event(
        trade_group_id="g1",
        settlement_key=fills[0].settlement_key,
        category="mlb_prop_paper",
        venue="polymarket_us",
        market_id="poly-1",
        event_type="settlement",
        payout=Decimal("2"),
        ts_ms=150,
    )
    settlements, audits = settlement_events(fills + [existing], [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "YES",
        "kalshi_outcome": "YES",
        "settled_ts_ms": 200,
    }])
    assert len(settlements) == 1
    assert settlements[0].venue == "kalshi"
    assert audits[0]["settlement_events_created"] == 1


def test_wrong_market_same_venue_settlement_does_not_suppress_filled_leg():
    fills = _fills()
    wrong_market = make_event(
        trade_group_id="g1",
        settlement_key=fills[0].settlement_key,
        category="mlb_prop_paper",
        venue="polymarket_us",
        market_id="poly-other",
        event_type="settlement",
        payout=Decimal("0"),
        ts_ms=150,
    )
    settlements, audits = settlement_events(fills + [wrong_market], [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "YES",
        "kalshi_outcome": "YES",
        "settled_ts_ms": 200,
    }])
    assert len(settlements) == 2
    by_venue = {event.venue: event for event in settlements}
    assert by_venue["polymarket_us"].market_id == "poly-1"
    assert by_venue["kalshi"].market_id == "kalshi-1"
    assert audits[0]["settlement_events_created"] == 2
    summary = summarize_ledger(fills + [wrong_market] + settlements)
    assert summary["pending_settlement_groups"] == 0
    assert Decimal(summary["realized_pnl"]) == Decimal("0.05")


def test_later_conflicting_outcome_quarantines_previously_written_settlements(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    outcomes_path = tmp_path / "outcomes.jsonl"
    fills = _fills()
    prior_settlements, _ = settlement_events(fills, [{
        "trade_group_id": "g1",
        "polymarket_us_outcome": "YES",
        "kalshi_outcome": "YES",
        "settled_ts_ms": 200,
    }])
    with ledger_path.open("w", encoding="utf-8") as fh:
        for event in fills + prior_settlements:
            fh.write(event.as_json() + "\n")
    with outcomes_path.open("w", encoding="utf-8") as fh:
        fh.write('{"trade_group_id":"g1","polymarket_us_outcome":"YES","kalshi_outcome":"YES","settled_ts_ms":200}\n')
        fh.write('{"trade_group_id":"g1","polymarket_us_outcome":"NO","kalshi_outcome":"YES","settled_ts_ms":201}\n')

    summary = reconcile_paper_ledger(ledger_path, outcomes_path)
    assert Decimal(summary["realized_pnl"]) == Decimal("0")
    assert summary["pending_settlement_groups"] == 1
    assert summary["outcome_conflict_groups"] == 1
    assert summary["quarantined_conflicted_trade_groups"] == 1
    assert summary["quarantined_stale_settlement_events"] == 2
    assert summary["new_settlement_events"] == 0
