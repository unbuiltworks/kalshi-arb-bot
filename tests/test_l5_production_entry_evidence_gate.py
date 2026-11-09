from src.l5_second_leg_economics import production_entry_evidence_gate


def evidence(**overrides):
    args = dict(up_price="0.40", down_price="0.55", quantity="1",
                up_fee="0.01", down_fee="0.01", adverse_cost="0.02",
                max_single_leg_loss="0.60", max_residual_probability="0.5",
                calibration_id="oos-v1", book_observation_id="venue-123",
                book_age_s="0.4", up_depth="2", down_depth="2",
                p_both="0.525", p_up_only="0.300",
                p_down_only="0.123", p_neither="0.052",
                p_up_settles_given_up_only="0.35",
                p_up_settles_given_down_only="0.65")
    args.update(overrides)
    return production_entry_evidence_gate(**args)


def test_missing_fee_refused_even_when_other_evidence_present():
    assert evidence(up_fee=None)["reason"] == "ECONOMIC_RISK_EVIDENCE_MISSING"


def test_missing_calibration_refused():
    assert evidence(calibration_id="")["reason"] == "CALIBRATION_OR_BOOK_ID_MISSING"


def test_missing_loss_budget_refused():
    assert evidence(max_single_leg_loss=None)["reason"] == "ECONOMIC_RISK_EVIDENCE_MISSING"


def test_valid_evidence_never_authorizes_live_orders():
    row = evidence()
    assert row["model_only"] is True
    assert row["production_authorized"] is False
    assert row["actual_quote_prices_used"] is True


def test_stale_book_refused():
    assert evidence(book_age_s="6.5")["reason"] == "STALE_BOOK"
