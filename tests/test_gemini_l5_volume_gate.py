from decimal import Decimal as D

import pytest

from src.gemini_l5_volume_gate import FiveMinuteVolumeGate, VolumeGateConfig, threshold_grid


def test_dead_window_never_opens() -> None:
    gate = FiveMinuteVolumeGate()
    decisions = [
        gate.observe(volume_contracts=D("0"), seconds_to_expiry=left)
        for left in (280, 270, 250, 220, 180, 120, 60)
    ]
    assert not any(d.allow_new_entries for d in decisions)
    assert decisions[-1].tier == "COLD"


def test_hot_early_velocity_opens_without_hindsight() -> None:
    gate = FiveMinuteVolumeGate()
    first = gate.observe(volume_contracts=D("0"), seconds_to_expiry=280)
    confirm = gate.observe(volume_contracts=D("20"), seconds_to_expiry=270)
    opened = gate.observe(volume_contracts=D("30"), seconds_to_expiry=265)

    assert first.reason == "WARMUP"
    assert confirm.reason == "FLOW_CONFIRMING"
    assert confirm.projected_final_contracts >= D("500")
    assert opened.allow_new_entries is True
    assert opened.reason == "FLOW_CONFIRMED_OPEN"
    assert opened.tier in {"HOT", "VERY_HOT"}


def test_observed_volume_can_open_even_when_recent_velocity_is_low() -> None:
    cfg = VolumeGateConfig(confirmations_to_open=2)
    gate = FiveMinuteVolumeGate(cfg)
    gate.observe(volume_contracts=D("150"), seconds_to_expiry=250)
    opened = gate.observe(volume_contracts=D("150"), seconds_to_expiry=244)
    assert opened.allow_new_entries is True
    assert opened.volume_contracts == D("150")


def test_open_gate_closes_only_after_hysteresis() -> None:
    cfg = VolumeGateConfig(confirmations_to_open=1, confirmations_to_close=3)
    gate = FiveMinuteVolumeGate(cfg)
    opened = gate.observe(volume_contracts=D("160"), seconds_to_expiry=260)
    assert opened.gate_open

    one = gate.observe(volume_contracts=D("160"), seconds_to_expiry=220)
    two = gate.observe(volume_contracts=D("160"), seconds_to_expiry=210)
    three = gate.observe(volume_contracts=D("160"), seconds_to_expiry=200)

    assert one.gate_open
    assert two.gate_open
    assert not three.gate_open
    assert three.reason == "FLOW_DECAYED_CLOSE"


def test_volume_decrease_fails_closed_for_window_identity_change() -> None:
    gate = FiveMinuteVolumeGate()
    gate.observe(volume_contracts=D("10"), seconds_to_expiry=270)
    with pytest.raises(ValueError, match="decreased"):
        gate.observe(volume_contracts=D("9"), seconds_to_expiry=260)


def test_invalid_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        FiveMinuteVolumeGate(VolumeGateConfig(warmup_seconds=300))
    with pytest.raises(ValueError):
        FiveMinuteVolumeGate(VolumeGateConfig(confirmations_to_open=0))


def test_threshold_grid_keeps_projected_and_observed_labels_separate() -> None:
    labels = threshold_grid(volume_contracts=D("90"), projected_final_contracts=D("720"))
    assert labels["projected_250_plus"]
    assert labels["projected_500_plus"]
    assert not labels["projected_1000_plus"]
    assert not labels["observed_250_plus"]
