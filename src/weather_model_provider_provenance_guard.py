"""Fail-closed provider provenance audit for read-only weather model sanity.

This companion guard closes a subtle independence gap: an anchor cannot be called
independent when the scored model omits the provider/component provenance needed to
compare organizational source families. It is intentionally read-only and does not
create execution eligibility.
"""
from __future__ import annotations

from typing import Any, Iterable


_PROVIDER_ALIASES = {
    "NWS": "NOAA",
    "NOAA": "NOAA",
    "GFS": "NOAA",
    "NOAA_GFS": "NOAA",
    "ECMWF": "ECMWF",
    "IFS": "ECMWF",
    "DWD": "DWD",
    "ICON": "DWD",
    "TWC": "TWC",
    "THE_WEATHER_COMPANY": "TWC",
    "WEATHER_COMPANY": "TWC",
    "WEATHER.COM": "TWC",
}

_ENSEMBLE_OR_DERIVED = {"ENSEMBLE", "MULTI_MODEL", "INTERNAL", "CUSTOM", "BLEND"}


def _family(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return _PROVIDER_ALIASES.get(text, text)


def _families(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raw: Iterable[Any] = [part for part in values.split(",") if part.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw = values
    else:
        return ()
    return tuple(sorted({_family(v) for v in raw if _family(v)}))


def audit_model_provider_provenance(
    *,
    model_provider: Any,
    model_component_providers: Any,
    independent_anchor_provider: Any,
) -> dict[str, Any]:
    """Prove enough provider provenance to evaluate anchor independence.

    A single-source model must declare its provider. An ensemble/derived/internal
    model must additionally enumerate its upstream provider families; otherwise an
    anchor from one of those hidden inputs could be falsely called independent.
    """
    blockers: list[str] = []
    model_family = _family(model_provider)
    components = set(_families(model_component_providers))
    anchor_family = _family(independent_anchor_provider)

    if not model_family:
        blockers.append("MODEL_PROVIDER_PROVENANCE_MISSING")
    if not anchor_family:
        blockers.append("ANCHOR_PROVIDER_PROVENANCE_MISSING")

    if model_family in _ENSEMBLE_OR_DERIVED and not components:
        blockers.append("MODEL_COMPONENT_PROVIDER_PROVENANCE_MISSING")

    source_families = set(components)
    if model_family and model_family not in _ENSEMBLE_OR_DERIVED:
        source_families.add(model_family)

    if anchor_family and anchor_family in source_families:
        blockers.append("ANCHOR_PROVIDER_NOT_INDEPENDENT")

    verified = not blockers
    return {
        "provider_provenance_verified": verified,
        "model_provider_family": model_family or None,
        "model_component_provider_families": sorted(components),
        "independent_anchor_provider_family": anchor_family or None,
        "blockers": sorted(set(blockers)),
        "classification": (
            "MODEL_PROVIDER_PROVENANCE_VERIFIED_SHADOW"
            if verified
            else "MODEL_PROVIDER_PROVENANCE_RESEARCH_ONLY"
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
