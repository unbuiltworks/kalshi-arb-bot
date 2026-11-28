"""Research-only market identity validation. Does not authorize orders."""
import re
from datetime import datetime, timezone

_PATTERN = re.compile(r"cpc(eth|sol|xrp)-\1-updown-(1h|1d)-(\d{4}-\d{2}-\d{2})-(\d{4})z", re.ASCII)

def identify(slug):
    if not isinstance(slug, str):
        return None
    m = _PATTERN.fullmatch(slug)
    if m is None:
        return None
    asset, duration, date, clock = m.groups()
    try:
        boundary = datetime.strptime(date + clock, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if boundary.minute != 0 or (duration == "1d" and boundary.hour != 22):
        return None
    return {"asset": asset, "duration": duration,
            "slug_boundary": boundary.isoformat(),
            "duration_seconds": 3600 if duration == "1h" else 86400}
