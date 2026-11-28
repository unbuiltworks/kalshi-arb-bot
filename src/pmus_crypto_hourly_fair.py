"""Research-only hourly binary fair model. No order or network access.

Not calibrated for trading and not wired into any engine. THEORETICAL only.

Venue contract (Polymarket US `cpc*-updown-1h-*`, market description and
`assetPriceTerms`, read 2026-11-28):

  "This market will settle to Up if the <asset> price at <end> is greater
  than or equal to the price at <start>. Otherwise, this market will settle
  to Down. Outcome sourced from CF Benchmarks' <index> ... The price at the
  beginning and at the end of the time range is determined the same way: in
  the last minute before each of those two times, 60 <index> prices are
  collected. The official value for that time is the simple average of these
  prices, rounded to the nearest <d> decimal places. Please note that this
  market is about the price according to CF Benchmarks' <index>, not
  according to other sources or spot markets."

So the inputs must be:
  strike  the venue's own `assetPriceTerms.priceToBeat` for this exact slug
          (the rounded 60-print average before `windowStart`);
  spot    the latest CF Benchmarks print of the SAME index the market names
          (`assetPriceTerms.indexSymbol`: BRTI, ETHUSD_RTI, SOLUSD_RTI,
          XRPUSD_RTI). An exchange spot price is NOT acceptable: a past
          cross-venue trade lost money to exactly that index mismatch.

Model: driftless log price, settle value = mean of 60 one-second prints in
[end-60, end), rounded; UP iff rounded mean >= strike (ties are UP).
Before the fixing minute the variance to the fixing is
  sigma^2 * ((end - 60 - now) + FIX_VAR_S)
where FIX_VAR_S = (n+1)(2n+1)/(6n) s is the variance (in seconds of
diffusion) of the mean of n=60 one-second samples. Inside the fixing minute
the already-collected prints are unknown here, so the function fails closed.
Zero LOG drift (median = spot); the martingale -sigma^2 tau/2 term is below
0.003 z over one hour at crypto vols and is deliberately ignored.
"""
import math

HORIZON_S = 3600
FIX_SAMPLES = 60
FIX_WINDOW_S = 60.0
FIX_VAR_S = (FIX_SAMPLES + 1) * (2 * FIX_SAMPLES + 1) / (6.0 * FIX_SAMPLES)
YEAR_S = 365.25 * 86400          # crypto indices print 24/7
#: settlement indices named by the venue for the four hourly series
SETTLEMENT_INDICES = frozenset({"BRTI", "ETHUSD_RTI", "SOLUSD_RTI",
                                "XRPUSD_RTI"})


def _num(x):
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    return float(x)


def hourly_fair(*, spot, strike, annual_vol, now, observed_at, start, end,
                reference_verified=False, max_age=5.0, spot_index=None,
                settlement_index=None, price_decimals=None):
    """Return P(UP) or None (fail closed). Timestamps are UTC epoch seconds.

    reference_verified  caller asserts `strike` is the venue priceToBeat of
                        this slug (not a self-computed open).
    spot_index /        the index `spot` was read from and the market's
    settlement_index    indexSymbol; both required, equal, and a known RTI.
    price_decimals      the venue's rounding of the official value
                        (assetPriceTerms.priceDecimals); when given, the
                        rounded-tie rule shifts the strike by half a unit.
    """
    if reference_verified is not True:
        return None
    if not (isinstance(spot_index, str) and spot_index == settlement_index
            and spot_index in SETTLEMENT_INDICES):
        return None
    try:
        spot, strike, annual_vol, now, observed_at, start, end, max_age = (
            _num(x) for x in (spot, strike, annual_vol, now, observed_at,
                              start, end, max_age))
        half = 0.0
        if price_decimals is not None:
            if isinstance(price_decimals, bool) or \
                    int(price_decimals) != price_decimals or \
                    not 0 <= int(price_decimals) <= 12:
                return None
            half = 0.5 * 10.0 ** -int(price_decimals)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) for x in (
            spot, strike, annual_vol, now, observed_at, start, end, max_age)):
        return None
    if min(spot, strike, annual_vol, max_age) <= 0 or strike - half <= 0:
        return None
    if end - start != HORIZON_S or start % HORIZON_S != 0:
        return None
    # inside the fixing minute the collected prints matter and are unknown
    if not start <= now < end - FIX_WINDOW_S:
        return None
    if not start <= observed_at <= now or now - observed_at > max_age:
        return None
    var_s = (end - FIX_WINDOW_S - now) + FIX_VAR_S
    sigma = annual_vol * math.sqrt(var_s / YEAR_S)
    if sigma <= 0 or not math.isfinite(sigma):
        return None
    z = (math.log(spot) - math.log(strike - half)) / sigma
    return 0.5 * math.erfc(-z / math.sqrt(2))
