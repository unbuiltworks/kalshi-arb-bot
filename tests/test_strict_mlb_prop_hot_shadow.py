from src.strict_mlb_prop_hot_shadow import _readiness_fields


def test_preferred_hot_packet_is_not_manual_or_live_ready():
    fields = _readiness_fields(preferred=True)

    assert fields["label"] == "PREFERRED_RULES_PENDING_SHADOW"
    assert fields["participation_cleared"] is True
    assert fields["participation_gate_equivalent_now"] is True
    assert fields["full_rules_audited"] is False
    assert fields["rules_status"] == "RULES_PENDING"
    assert fields["fully_audited_shadow_ready"] is False
    assert fields["live_probe_ready"] is False
    assert "MANUAL_PROBE_READY" not in fields.values()


def test_positive_hot_packet_stays_rules_pending():
    fields = _readiness_fields(preferred=False)

    assert fields["label"] == "POSITIVE_RULES_PENDING_SHADOW"
    assert fields["full_rules_audited"] is False
    assert fields["fully_audited_shadow_ready"] is False
    assert fields["live_probe_ready"] is False
