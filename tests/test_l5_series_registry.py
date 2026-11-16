"""THE series registry: one table, and what adding a row does NOT do."""
from __future__ import annotations

import re

import pytest

from src import l5_series_registry as R


class TestItReproducesTheLiteralsItReplaced:
    """The 34 files that held "cpc-btc-updown-5m-" must see exactly what
    they saw before. Identical by construction, pinned here by value."""

    def test_the_two_existing_series_are_unchanged(self):
        assert R.BY_STRATEGY["BTC15M"].prefix == "cpc-btc-updown-15m-"
        assert R.BY_STRATEGY["BTC5M"].prefix == "cpc-btc-updown-5m-"
        assert R.BY_STRATEGY["BTC15M"].horizon_s == 900.0
        assert R.BY_STRATEGY["BTC5M"].horizon_s == 300.0
        assert R.BY_STRATEGY["BTC15M"].coid_prefix == "l5f-"
        assert R.BY_STRATEGY["BTC5M"].coid_prefix == "b5m-"

    def test_the_venue_marker_matches_what_the_transport_used(self):
        assert R.BY_STRATEGY["BTC15M"].venue_marker == "btc-updown-15m"
        assert R.BY_STRATEGY["BTC5M"].venue_marker == "btc-updown-5m"

    def test_every_marker_is_a_substring_of_its_own_slug_and_no_others(self):
        """The transport gate is a substring test against the market's own
        slug, so a marker that is not in its slug refuses every order, and
        one that is in ANOTHER series' slug would accept the wrong market."""
        for a in R.REGISTRY:
            assert a.venue_marker in (a.prefix + "2026-11-15-2205z")
            for b in R.REGISTRY:
                if a is not b and a.horizon_s == b.horizon_s:
                    assert a.venue_marker not in (b.prefix + "2026-11-15-2205z")

    def test_the_derived_consumers_agree(self):
        from src import l5_campaign_approval as CA
        from src import l5_origin as O
        from src import l5_pmus_transport as T
        assert CA.ALLOWED_SERIES[:2] == ("cpc-btc-updown-15m-",
                                        "cpc-btc-updown-5m-")
        assert CA.SERIES_WINDOW_S["cpc-btc-updown-15m-"] == 900.0
        assert CA.SERIES_WINDOW_S["cpc-btc-updown-5m-"] == 300.0
        assert O.STRATEGY_COID_PREFIX["BTC15M"] == "l5f-"
        assert O.STRATEGY_COID_PREFIX["BTC5M"] == "b5m-"
        assert T.SERIES["BTC5M"][0] == "btc-updown-5m"
        assert T.SERIES["BTC5M"][2] == "slug_is_btc5m_series"
        assert T.SERIES["BTC15M"][3] == "market_series_is_btc15m"


class TestSlugMatching:

    def test_a_real_window_resolves_to_its_series(self):
        s = R.for_slug("cpc-btc-updown-5m-2026-11-15-1925z")
        assert s is not None and s.strategy == "BTC5M"

    @pytest.mark.parametrize("bad", [
        "cpc-btc-updown-5m-garbage",            # prefix only is NOT enough
        "cpc-btc-updown-5m-2026-11-15-1925",    # no trailing z
        "cpc-btc-updown-5m-2026-11-15-192z",    # short time
        " cpc-btc-updown-5m-2026-11-15-1925z",  # stripped, then matched
        "xcpc-btc-updown-5m-2026-11-15-1925z",  # not anchored at the start
        "cpc-eth-updown-5m-2026-11-15-1925z",   # a series with no row
        "", None,
    ])
    def test_anything_else_belongs_to_nothing(self, bad):
        if bad == " cpc-btc-updown-5m-2026-11-15-1925z":
            assert R.for_slug(bad) is not None      # stripped first
            return
        assert R.for_slug(bad) is None

    def test_the_pattern_is_built_from_the_prefix(self):
        """A row cannot disagree with its own pattern, which is how two
        hand-kept literals drift apart."""
        for r in R.REGISTRY:
            good = r.prefix + "2026-11-15-1925z"
            assert r.slug_re.match(good)
            assert r.slug_re.pattern.startswith("^" + re.escape(r.prefix))


class TestAddingARowIsOneChangeAndNotAPermission:

    #: a HYPOTHETICAL row. It must name an asset that is NOT registered,
    #: or every check below trips on the duplicate instead of the property
    #: under test.
    ETH = R.Series("cpcdoge-doge-updown-5m-", "doge", 300.0, "DOGE5M", "d5m-",
                   "DOGEUSD_RTI")

    def test_a_new_row_needs_nothing_else_to_be_recognised(self):
        R._check(R.REGISTRY + (self.ETH,))               # accepted
        assert self.ETH.slug_re.match("cpcdoge-doge-updown-5m-2026-11-15-1925z")
        assert self.ETH.venue_marker == "doge-updown-5m"
        # and it is the part the slug itself repeats, which is what the
        # transport's market_series_is_* gate substring-matches
        assert self.ETH.venue_marker in (self.ETH.prefix + "2026-11-15-1925z")

    def test_a_duplicate_coid_prefix_is_refused(self):
        """Two strategies sharing a client-order-id prefix are
        indistinguishable in the ownership registry -- the confusion the
        watchdog latches on."""
        clash = self.ETH._replace(coid_prefix="b5m-")
        with pytest.raises(ValueError, match="client-order-id"):
            R._check(R.REGISTRY + (clash,))

    def test_a_duplicate_prefix_or_strategy_is_refused(self):
        with pytest.raises(ValueError, match="series prefix"):
            R._check(R.REGISTRY + (self.ETH._replace(
                prefix="cpc-btc-updown-5m-"),))
        with pytest.raises(ValueError, match="strategy id"):
            R._check(R.REGISTRY + (self.ETH._replace(strategy="BTC5M"),))

    @pytest.mark.parametrize("bad", [
        "doge-updown-5m-", "cpcdoge-doge-updown-5m", "cpc-DOGE-updown-5m-",
        "cpc-doge-upordown-5m-", "cpc-d-updown-5m-",
    ])
    def test_a_prefix_outside_the_venue_shape_is_refused(self, bad):
        with pytest.raises(ValueError, match="venue's shape"):
            R._check(R.REGISTRY + (self.ETH._replace(prefix=bad),))

    def test_a_row_grants_no_exposure(self):
        """The per-series cap tables are keyed by prefix. A new series has
        no entry, so it gets the GENERAL ceiling, which is tighter than any
        per-series grant -- a forgotten cap fails closed."""
        from src import l5_campaign_approval as CA
        assert self.ETH.prefix not in CA.SERIES_MAX_TOTAL_EXPOSURE_USD
        assert CA.SERIES_MAX_TOTAL_EXPOSURE_USD[R.BY_STRATEGY["BTC5M"].prefix] \
            < CA.MAX_TOTAL_EXPOSURE_USD

    def test_a_registered_row_IS_attributable_and_that_is_deliberate(self):
        """ORIGINS derives from the registry, so registering a series makes
        it attributable at once. That is the safe direction: an order no
        lane can attribute is an unattributable automated order, which
        LATCHES THE SHARED HALT -- measured twice on 2026-11-15. A row that
        could place but not be recognised would stop the whole account.

        Attributable is not fundable: the capital test above is what keeps
        a new row from trading size."""
        from src import l5_origin as O
        for r in R.REGISTRY:
            assert r.strategy in O.ORIGINS
        assert self.ETH.strategy not in O.ORIGINS      # unregistered


class TestTheShippedTableIsSane:

    def test_the_registry_passes_its_own_checks(self):
        R._check(R.REGISTRY)

    def test_every_image_that_imports_a_consumer_ships_this_module(self):
        """The defect that recurred six times on 2026-11-14: the module is
        correct, the image does not carry it, and it is an ImportError in
        production."""
        import glob
        import os
        consumers = ("l5_origin.py", "l5_campaign_approval.py",
                     "l5_pmus_transport.py")
        missing = []
        for df in glob.glob(os.path.join("deploy", "*", "Dockerfile")):
            src = open(df).read()
            if any(c in src for c in consumers) \
                    and "l5_series_registry.py" not in src:
                missing.append(df)
        assert missing == [], missing

    def test_it_imports_only_stdlib(self):
        """A naming authority must not drag a credential or a venue client
        into every image that needs a series name."""
        import inspect
        src = inspect.getsource(R)
        assert "from src" not in src and "import src" not in src


class TestTheFourFiveMinuteSeries:
    """All four were MARKET_STATUS_OPEN on the same window at
    2026-11-15T22:06Z. The prefixes are IRREGULAR, which is why they are
    written down from the venue's own answer rather than derived: BTC is
    "cpc-btc-", every other asset repeats its ticker, "cpceth-eth-". 64
    variants extrapolated from BTC all 404'd."""

    REAL = {
        "BTC5M": "cpc-btc-updown-5m-2026-11-15-2205z",
        "ETH5M": "cpceth-eth-updown-5m-2026-11-15-2205z",
        "SOL5M": "cpcsol-sol-updown-5m-2026-11-15-2205z",
        "XRP5M": "cpcxrp-xrp-updown-5m-2026-11-15-2205z",
    }

    def test_all_four_are_registered_at_five_minutes(self):
        assert R.prefixes(300.0) == (
            "cpc-btc-updown-5m-", "cpceth-eth-updown-5m-",
            "cpcsol-sol-updown-5m-", "cpcxrp-xrp-updown-5m-")

    @pytest.mark.parametrize("strategy,slug", sorted(REAL.items()))
    def test_a_real_venue_slug_resolves_to_its_strategy(self, strategy, slug):
        s = R.for_slug(slug)
        assert s is not None and s.strategy == strategy and s.horizon_s == 300.0

    def test_no_slug_resolves_to_another_asset(self):
        for strategy, slug in self.REAL.items():
            for other in R.REGISTRY:
                if other.strategy != strategy:
                    assert not other.slug_re.match(slug), (strategy, other)

    def test_the_btc_shaped_guess_for_eth_is_NOT_a_series(self):
        """"cpc-eth-updown-5m-" is what extrapolating from BTC produces and
        is not what the venue serves. It must resolve to nothing."""
        assert R.for_slug("cpc-eth-updown-5m-2026-11-15-2205z") is None

    def test_each_carries_the_same_five_dollar_ceiling_as_btc5m(self):
        """REQUIRED, not optional: with no per-series row a series inherits
        the general $20, four times what the 5m strategy is approved for.
        This is the one place where a missing row fails OPEN."""
        from src import l5_campaign_approval as CA
        for pre in R.prefixes(300.0):
            assert CA.SERIES_MAX_TOTAL_EXPOSURE_USD[pre] == CA.D("5")

    def test_every_strategy_is_attributable(self):
        from src import l5_origin as O
        for r in R.REGISTRY:
            assert r.strategy in O.ORIGINS
            assert O.STRATEGY_COID_PREFIX[r.strategy] == r.coid_prefix




# ======================================================================
# ONE PROCESS, ONE SERIES -- CHOSEN BY CONFIGURATION
# ======================================================================
class TestTheExecutorsStrategyIsConfigurable:
    """`L5_5M_STRATEGY` picks which 5-minute series this executor trades.

    One process still trades ONE series; that is not negotiable, because
    the client-order-id prefix is how the ownership registry tells lanes
    apart. What changed is that the choice is configuration rather than a
    welded-in constant, so ETH/SOL/XRP need another SERVICE, not another
    executor."""

    @staticmethod
    def _reload(monkeypatch, value):
        import importlib
        from src import l5_btc5m_execution as X
        if value is None:
            monkeypatch.delenv("L5_5M_STRATEGY", raising=False)
        else:
            monkeypatch.setenv("L5_5M_STRATEGY", value)
        return importlib.reload(X)

    def test_unset_is_btc5m_so_every_deployment_is_unchanged(self, monkeypatch):
        X = self._reload(monkeypatch, None)
        assert (X.STRATEGY, X.COID_PREFIX, X.MARKET_PREFIX) == (
            "BTC5M", "b5m-", "cpc-btc-updown-5m-")

    @pytest.mark.parametrize("strategy,coid,prefix", [
        ("ETH5M", "e5m-", "cpceth-eth-updown-5m-"),
        ("SOL5M", "s5m-", "cpcsol-sol-updown-5m-"),
        ("XRP5M", "x5m-", "cpcxrp-xrp-updown-5m-"),
        ("eth5m", "e5m-", "cpceth-eth-updown-5m-"),      # case-insensitive
    ])
    def test_each_registered_5m_asset_can_be_selected(
            self, monkeypatch, strategy, coid, prefix):
        X = self._reload(monkeypatch, strategy)
        assert X.STRATEGY == strategy.upper()
        assert X.COID_PREFIX == coid and X.MARKET_PREFIX == prefix

    def test_an_unregistered_name_refuses_at_import(self, monkeypatch):
        """A service that cannot say which series it trades must not
        start."""
        with pytest.raises(ValueError, match="not a registered strategy"):
            self._reload(monkeypatch, "DOGE5M")

    def test_a_fifteen_minute_series_refuses(self, monkeypatch):
        """The hazard this closes: pointing the FIVE-minute executor at the
        15-minute lane would write b5m- orders into BTC15M's series, making
        the two indistinguishable in the ownership registry -- which is
        exactly what the watchdog latches on."""
        with pytest.raises(ValueError, match="FIVE-MINUTE"):
            self._reload(monkeypatch, "BTC15M")

    def test_the_runner_follows_the_executor(self, monkeypatch):
        """The runner must not keep its own idea of the series. It had two
        hardcoded series="BTC5M" literals; both now read X.STRATEGY."""
        import inspect
        from src import l5_btc5m_runner as RN
        src = inspect.getsource(RN)
        assert 'series="BTC5M"' not in src
        assert 'series=X.STRATEGY' in src

    def test_selecting_an_asset_does_not_grant_it_capital(self, monkeypatch):
        """Choosing a series is not funding one. Each still meets the same
        $5 per-series ceiling, and arming is still a separate owner act."""
        from src import l5_campaign_approval as CA
        X = self._reload(monkeypatch, "ETH5M")
        assert CA.SERIES_MAX_TOTAL_EXPOSURE_USD[X.MARKET_PREFIX] == CA.D("5")
        self._reload(monkeypatch, None)


class TestEveryRowCarriesTheVenuesIndexSymbol:
    """The probability engine asks the venue for a strike and checks the
    returned `assetPriceTerms.indexSymbol` against the series it asked
    about. That check is what reported ETH/SOL/XRP as UNEXPECTED_INDEX
    instead of pricing them against BTC's index.

    VERIFIED AGAINST THE VENUE 2026-11-16: 8 consecutive 5-minute windows
    per asset, 32 of 32 exact -- the 1-second bucket ending at
    windowStart, tickCount 60, equals priceToBeat to the cent.
    """

    def test_the_registered_symbols_are_the_ones_the_venue_reports(self):
        got = {r.strategy: r.index_symbol for r in R.REGISTRY}
        assert got == {"BTC15M": "BRTI", "BTC5M": "BRTI",
                       "ETH5M": "ETHUSD_RTI", "SOL5M": "SOLUSD_RTI",
                       "XRP5M": "XRPUSD_RTI"}

    def test_it_is_not_derivable_from_the_asset(self):
        """BTC is BRTI while the others are <TICKER>USD_RTI, so the table
        must carry it rather than compute it."""
        rows = {r.asset: r.index_symbol for r in R.REGISTRY}
        assert rows["btc"] == "BRTI"
        assert rows["eth"] == "ETHUSD_RTI"
        assert rows["btc"] != "BTCUSD_RTI"

    def test_a_row_without_one_is_refused(self):
        bad = R.Series("cpcdoge-doge-updown-5m-", "doge", 300.0, "DOGE5M",
                       "d5m-", "")
        with pytest.raises(ValueError, match="index symbol"):
            R._check(R.REGISTRY + (bad,))

    @pytest.mark.parametrize("sym", ["brti", "B", "has space", "x" * 25, "1RTI"])
    def test_a_symbol_outside_the_venues_shape_is_refused(self, sym):
        bad = R.Series("cpcdoge-doge-updown-5m-", "doge", 300.0, "DOGE5M",
                       "d5m-", sym)
        with pytest.raises(ValueError, match="index symbol"):
            R._check(R.REGISTRY + (bad,))

    def test_one_asset_may_not_carry_two_index_symbols(self):
        """btc 5m and 15m SHARE BRTI, which is correct. Disagreeing about
        what the venue publishes for one asset is not."""
        bad = R.Series("cpc-btc-updown-30m-", "btc", 1800.0, "BTC30M",
                       "b30-", "BTCUSD_RTI")
        with pytest.raises(ValueError, match="two index symbols"):
            R._check(R.REGISTRY + (bad,))

    def test_sharing_an_index_across_horizons_is_allowed(self):
        ok = R.Series("cpc-btc-updown-30m-", "btc", 1800.0, "BTC30M",
                      "b30-", "BRTI")
        R._check(R.REGISTRY + (ok,))
