from src import mlb_kp_rule_risk_parallel_entrypoint as policy


def _row(*, status, legs_filled, reserved="1", strategy="sports_kp_rule_risk_q1", metadata=None):
    body = {
        "mode": "LIVE_PROBE",
        "strategy": strategy,
        "status": status,
        "legs_filled": legs_filled,
        "capital_filled": "0.98" if legs_filled else "0",
        "trade_id": "t1",
        "metadata": {"reserved_usd": reserved},
    }
    if metadata:
        body["metadata"].update(metadata)
    return body


def test_two_leg_awaiting_settlement_does_not_serialize():
    rows = [_row(status="FILLED_NOT_SETTLED", legs_filled=2)]
    assert policy._safe_unresolved(rows) is None


def test_one_leg_still_blocks_everything():
    rows = [_row(status="ONE_LEG_FILLED", legs_filled=1)]
    assert policy._safe_unresolved(rows) is rows[0]


def test_reserved_only_still_blocks_everything():
    rows = [_row(status="CAPITAL_RESERVED", legs_filled=0)]
    assert policy._safe_unresolved(rows) is rows[0]


def test_hard_halt_metadata_blocks_even_with_two_legs():
    rows = [_row(
        status="FILLED_NOT_SETTLED",
        legs_filled=2,
        metadata={"execution": {"hard_halt_required": True}},
    )]
    assert policy._safe_unresolved(rows) is rows[0]


def test_exact_pair_duplicate_guard_uses_venue_ids_not_player_text():
    rows = [_row(
        status="FILLED_NOT_SETTLED",
        legs_filled=2,
        metadata={
            "plan": {
                "candidate": {
                    "poly_slug": "astatc-mlb-x-player-gte2",
                    "kalshi_ticker": "KXMLBTB-X-2",
                }
            }
        },
    )]
    assert policy._already_filled(
        rows,
        poly_slug="astatc-mlb-x-player-gte2",
        kalshi_ticker="KXMLBTB-X-2",
    ) == "t1"
    assert policy._already_filled(
        rows,
        poly_slug="astatc-mlb-x-player-gte3",
        kalshi_ticker="KXMLBTB-X-3",
    ) is None


def test_filled_pair_set_is_restart_persistent_from_ledger_metadata():
    rows = [_row(
        status="SETTLED",
        legs_filled=2,
        metadata={
            "execution": {
                "plan": {
                    "candidate": {
                        "poly_slug": "astatc-mlb-y-player-gte1",
                        "kalshi_ticker": "KXMLBHR-Y-1",
                    }
                }
            }
        },
    )]
    assert policy._filled_pairs(rows) == {("astatc-mlb-y-player-gte1", "KXMLBHR-Y-1")}
