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
    ETH = R.Series("cpcdoge-doge-updown-5m-", "doge", 300.0, "DOGE5M", "d5m-")

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


class TestTheVerifiedButUnregisteredSeries:
    """ETH/SOL/XRP 5m were confirmed OPEN on the venue at 2026-11-15T22:06Z
    and their exact prefixes are recorded in the module, NOT registered --
    registering them changes the approved production series allowlist,
    which is an owner decision. These pin what is true today."""

    READY = ("cpceth-eth-updown-5m-", "cpcsol-sol-updown-5m-",
             "cpcxrp-xrp-updown-5m-")

    def test_they_are_documented_with_their_real_prefixes(self):
        import inspect
        src = inspect.getsource(R)
        for pre in self.READY:
            assert pre in src

    def test_they_are_NOT_tradeable_until_registered(self):
        from src import l5_campaign_approval as CA
        from src import l5_origin as O
        for pre in self.READY:
            assert pre not in R.BY_PREFIX
            assert pre not in CA.ALLOWED_SERIES
            assert R.for_slug(pre + "2026-11-15-2205z") is None
        for strat in ("ETH5M", "SOL5M", "XRP5M"):
            assert strat not in O.ORIGINS

    def test_each_would_be_accepted_by_the_registry_checks(self):
        """The rows are ready: valid shape, unique prefixes, unique coids."""
        rows = R.REGISTRY + (
            R.Series("cpceth-eth-updown-5m-", "eth", 300.0, "ETH5M", "e5m-"),
            R.Series("cpcsol-sol-updown-5m-", "sol", 300.0, "SOL5M", "s5m-"),
            R.Series("cpcxrp-xrp-updown-5m-", "xrp", 300.0, "XRP5M", "x5m-"))
        R._check(rows)
        for r in rows:
            assert r.venue_marker in (r.prefix + "2026-11-15-2205z")

    def test_the_btc_shaped_guess_is_not_the_real_prefix(self):
        """"cpc-eth-updown-5m-" is what extrapolating from BTC produces.
        The venue serves "cpceth-eth-updown-5m-". 64 guessed variants 404'd
        before the event payload gave the real one."""
        assert "cpc-eth-updown-5m-" not in self.READY
