"""Fail-closed exact-market weather settlement-source audit.

Read-only research helper. This intentionally treats the exact market/date rules as
higher authority than the *current* recurring Series settlement-source snapshot so
historical contracts cannot be misclassified across a source transition.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

_ALLOWED_KALSHI_HOSTS = {
    "kalshi.com",
    "www.kalshi.com",
    "assets.kalshi.com",
    "kalshi-public-docs.s3.amazonaws.com",
}


def _https_host(url: Any) -> tuple[str, str] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return None
    return host, parsed.path or "/"


def _official_kalshi_evidence(url: Any) -> bool:
    parsed = _https_host(url)
    return parsed is not None and parsed[0] in _ALLOWED_KALSHI_HOSTS


def _official_exact_market_evidence(url: Any, event_ticker: str) -> bool:
    """Require market-page evidence to contain the exact event path token.

    A substring check is not sufficient here: e.g. an evidence page for
    ``...-26AUG120`` must never certify ``...-26AUG12``.  Treat URL path segments as
    identifiers and require exact, case-insensitive equality after URL decoding.
    """
    parsed = _https_host(url)
    if parsed is None or not event_ticker:
        return False
    host, path = parsed
    if host not in {"kalshi.com", "www.kalshi.com"}:
        return False
    normalized_path = path.rstrip("/")
    if not normalized_path.lower().startswith("/markets/"):
        return False
    event_token = event_ticker.strip().lower()
    path_tokens = {
        unquote(token).strip().lower()
        for token in normalized_path.split("/")
        if token.strip()
    }
    return event_token in path_tokens


def _official_nws_cli_source(source_url: Any) -> bool:
    """Require the station-specific NWS Daily Climate Report product, not any weather.gov page."""
    if not isinstance(source_url, str) or not source_url.strip():
        return False
    try:
        parsed = urlparse(source_url.strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not (host == "weather.gov" or host == "www.weather.gov" or host.endswith(".weather.gov")):
        return False
    if (parsed.path or "").rstrip("/").lower() != "/product.php":
        return False
    query = {k.lower(): [v.upper() for v in values] for k, values in parse_qs(parsed.query).items()}
    product = query.get("product", [])
    issuedby = query.get("issuedby", [])
    return "CLI" in product and any(token.strip() for token in issuedby)


def _recognized_source(provider: Any, source_url: Any) -> str | None:
    p = str(provider or "").strip().upper()
    parsed = _https_host(source_url)
    if parsed is None:
        return None
    host, path = parsed
    if p == "THE_WEATHER_COMPANY" and host in {"weather.com", "www.weather.com"} and path.rstrip("/").lower() == "/kalshi":
        return p
    if p == "NWS" and _official_nws_cli_source(source_url):
        return p
    return None


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%d") == value


def audit_exact_market_source(
    *,
    series_ticker: Any,
    event_ticker: Any,
    market_ticker: Any,
    market_date: Any,
    provider: Any,
    source_url: Any,
    evidence_url: Any,
    evidence_market_ticker: Any,
    evidence_market_date: Any,
) -> dict[str, Any]:
    """Validate exact-market settlement-source provenance without using current Series provider as truth.

    The returned audit preserves the *evidence-side* market ticker/date separately from
    the target market identity. Downstream trust boundaries must replay those explicit
    fields rather than substituting the target identity when evidence identity is absent.
    """
    blockers: list[str] = []
    s = str(series_ticker or "").strip().upper()
    e = str(event_ticker or "").strip().upper()
    m = str(market_ticker or "").strip().upper()
    em = str(evidence_market_ticker or "").strip().upper()
    md = str(market_date or "").strip()
    ed = str(evidence_market_date or "").strip()

    if not s or not e.startswith(s + "-") or not m.startswith(e + "-"):
        blockers.append("EXACT_MARKET_IDENTITY_UNVERIFIED")
    if not _iso_date(md) or ed != md or em != m:
        blockers.append("EXACT_MARKET_EVIDENCE_MISMATCH")
    if not _official_kalshi_evidence(evidence_url):
        blockers.append("EXACT_MARKET_EVIDENCE_UNOFFICIAL")
    elif not _official_exact_market_evidence(evidence_url, e):
        blockers.append("EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT")

    recognized = _recognized_source(provider, source_url)
    if recognized is None:
        blockers.append("EXACT_MARKET_SETTLEMENT_SOURCE_UNVERIFIED")

    blockers = sorted(set(blockers))
    exact_evidence_ok = _official_exact_market_evidence(evidence_url, e)
    return {
        "verified": not blockers,
        "blockers": blockers,
        "series_ticker": s or None,
        "event_ticker": e or None,
        "market_ticker": m or None,
        "market_date": md or None,
        "evidence_market_ticker": em or None,
        "evidence_market_date": ed or None,
        "settlement_source_provider": recognized,
        "settlement_source_url": str(source_url).strip() if recognized else None,
        "evidence_url": str(evidence_url).strip() if exact_evidence_ok else None,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
