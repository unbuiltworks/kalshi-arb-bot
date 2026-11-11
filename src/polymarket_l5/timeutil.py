"""Timestamp helpers. Everything internal is integer epoch seconds, UTC."""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional


def to_epoch_s(value: Any) -> Optional[int]:
    """Coerce a Polymarket timestamp (epoch s, epoch ms, or ISO-8601) to epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        # Polymarket mixes seconds and milliseconds across endpoints.
        if num > 1e12:
            num /= 1000.0
        if num <= 0:
            return None
        return int(num)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            return to_epoch_s(float(text))
        iso = text.replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return int(parsed.astimezone(dt.timezone.utc).timestamp())
    return None


def iso_utc(epoch_s: Optional[int]) -> str:
    if epoch_s is None:
        return ""
    return (
        dt.datetime.fromtimestamp(int(epoch_s), tz=dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
