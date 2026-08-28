from src.weather_model_provider_provenance_guard import audit_model_provider_provenance


def test_single_source_model_with_independent_anchor_can_pass_shadow_only():
    out = audit_model_provider_provenance(
        model_provider="NOAA",
        model_component_providers=None,
        independent_anchor_provider="TWC",
    )
    assert out["provider_provenance_verified"] is True
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_missing_model_provider_fails_closed():
    out = audit_model_provider_provenance(
        model_provider=None,
        model_component_providers=None,
        independent_anchor_provider="TWC",
    )
    assert out["provider_provenance_verified"] is False
    assert "MODEL_PROVIDER_PROVENANCE_MISSING" in out["blockers"]


def test_ensemble_requires_explicit_component_provider_provenance():
    out = audit_model_provider_provenance(
        model_provider="ENSEMBLE",
        model_component_providers=None,
        independent_anchor_provider="TWC",
    )
    assert out["provider_provenance_verified"] is False
    assert "MODEL_COMPONENT_PROVIDER_PROVENANCE_MISSING" in out["blockers"]


def test_hidden_same_provider_cannot_be_called_independent():
    out = audit_model_provider_provenance(
        model_provider="ENSEMBLE",
        model_component_providers=["NOAA", "ECMWF", "DWD"],
        independent_anchor_provider="NWS",
    )
    assert out["provider_provenance_verified"] is False
    assert "ANCHOR_PROVIDER_NOT_INDEPENDENT" in out["blockers"]


def test_provider_aliases_collapse_to_same_organization_family():
    out = audit_model_provider_provenance(
        model_provider="GFS",
        model_component_providers=None,
        independent_anchor_provider="NOAA",
    )
    assert out["provider_provenance_verified"] is False
    assert out["model_provider_family"] == "NOAA"
    assert out["independent_anchor_provider_family"] == "NOAA"
    assert "ANCHOR_PROVIDER_NOT_INDEPENDENT" in out["blockers"]


def test_internal_blend_with_declared_components_can_use_outside_anchor():
    out = audit_model_provider_provenance(
        model_provider="INTERNAL",
        model_component_providers=["NOAA", "ECMWF", "DWD"],
        independent_anchor_provider="TWC",
    )
    assert out["provider_provenance_verified"] is True
