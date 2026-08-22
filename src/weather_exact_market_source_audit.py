"""Fail-closed exact-market weather settlement-source audit.

Read-only research helper. This intentionally treats the exact market/date rules as
higher authority than the *current* recurring Series settlement-source snapshot so
historical contracts cannot be misclassified across a source transition.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    """Require market-page evidence to be bound to the exact event, not merely Kalshi-owned.

    A generic Kalshi homepage, Series page, or terms document is useful rules evidence but
    cannot prove which settlement-source regime applied to one historical exact market.
    Current public exact-market pages include the event ticker in their `/markets/...`
    path, so require that binding here. If Kalshi changes the URL shape, fail closed until
    the extractor is independently updated rather than silently accepting generic proof.
    """
    parsed = _https_host(url)
    if parsed is None or not event_ticker:
        return False
    host, path = parsed
    if host not in {"kalshi.com", "www.kalshi.com"}:
        return False
    normalized_path = path.rstrip("/").lower()
    return normalized_path.startswith("/markets/") and event_ticker.lower() in normalized_path


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

    The caller must extract ``provider``/``source_url`` from exact-market/date rules or
    equivalent official Kalshi evidence. A changed current Series template is not
    allowed to override that exact historical evidence.
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
        "settlement_source_provider": recognized,
        "settlement_source_url": str(source_url).strip() if recognized else None,
        "evidence_url": str(evidence_url).strip() if exact_evidence_ok else None,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
