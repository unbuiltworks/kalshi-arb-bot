from decimal import Decimal

from src.mlb_prop_paper_microprobe import build_paper_probes


def _row(*, profit="0.04", valid=True):
    profit_d = Decimal(profit)
    poly_price = (Decimal("2") - profit_d - Decimal("0.03")) / Decimal("2") - Decimal("0.51")
    plan = {
        "direction": "POLY_YES_KALSHI_NO",
        "quantity": 2,
        "poly_price": str(poly_price),
        "kalshi_price": "0.51",
        "poly_fee": "0.01",
        "kalshi_fee_upper_bound": "0.02",
        "total_cost_upper_bound": str(Decimal("2") - profit_d),
        "capital_cap": "5",
        "conservative_locked_profit": profit,
    }
    return {
        "family": "baseball_player_hits",
        "game_date": "2026-08-18",
        "player": "Duplicate Player",
        "threshold": "1",
        "poly_slug": "poly-duplicate",
        "kalshi_ticker": "KXMLBHIT-DUPLICATE",
        "book_comparable": True,
        "synchronized_book_pair": valid,
        "quote_receive_skew_ms": 20 if valid else 900,
        "fetch_error": None,
        "required_participation_gate": "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE",
        "participation_proof": {"satisfied": True},
        "participation_gate_satisfied": True,
        "rules_status": "RULES_PENDING",
        "full_rules_audited": False,
        "fully_audited_shadow_ready": False,
        "execution_eligible": False,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
        "probe_1usd": None,
        "probe_5usd": plan,
    }


def test_invalid_first_duplicate_does_not_suppress_later_valid_row():
    invalid = _row(profit="0.50", valid=False)
    valid = _row(profit="0.04", valid=True)
    events, audits = build_paper_probes({"paper_probe_candidates": [invalid, valid]}, ts_ms=123)
    assert len(audits) == 1
    assert Decimal(audits[0]["modeled_profit"]) == Decimal("0.04")
    assert audits[0]["duplicate_identity_rows_considered"] == 2
    assert len([event for event in events if event.event_type == "fill"]) == 2


def test_duplicate_selection_is_order_independent_and_keeps_best_valid_economics():
    weak = _row(profit="0.04", valid=True)
    strong = _row(profit="0.09", valid=True)
    first_events, first_audits = build_paper_probes({"paper_probe_candidates": [weak, strong]}, ts_ms=456)
    second_events, second_audits = build_paper_probes({"paper_probe_candidates": [strong, weak]}, ts_ms=456)
    assert len(first_audits) == len(second_audits) == 1
    assert first_audits[0]["modeled_profit"] == second_audits[0]["modeled_profit"] == "0.09"
    assert first_audits[0]["opportunity_fingerprint"] == second_audits[0]["opportunity_fingerprint"]
    assert len([event for event in first_events if event.event_type == "fill"]) == 2
    assert len([event for event in second_events if event.event_type == "fill"]) == 2
