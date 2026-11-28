"""Research-only hourly binary fair model. No order or network access.

Inputs must come from verified settlement-compatible reference data.
Lognormal zero-log-drift baseline, not a calibrated trading signal.
"""
import math


def hourly_fair(*, spot, strike, annual_vol, now, observed_at, start, end,
                reference_verified=False, max_age=5.0):
    """Return UP probability or None. All timestamps are UTC epoch seconds."""
    if reference_verified is not True:
        return None
    try:
        spot, strike, annual_vol, now, observed_at, start, end, max_age = (
            float(x) for x in (spot, strike, annual_vol, now, observed_at,
                               start, end, max_age))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) for x in (
            spot, strike, annual_vol, now, observed_at, start, end, max_age)):
        return None
    if min(spot, strike, annual_vol, max_age) <= 0:
        return None
    if end - start != 3600 or not start <= now < end:
        return None
    if not start <= observed_at <= now or now - observed_at > max_age:
        return None
    sigma = annual_vol * math.sqrt((end - now) / (365.25 * 86400))
    if sigma <= 0 or not math.isfinite(sigma):
        return None
    z = (math.log(spot) - math.log(strike)) / sigma
    return 0.5 * math.erfc(-z / math.sqrt(2))
