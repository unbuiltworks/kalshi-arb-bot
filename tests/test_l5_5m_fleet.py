"""The 5-minute fleet: one service, one volume, one child per asset."""
from __future__ import annotations

import pytest

from src import l5_5m_fleet as F
from src import l5_origin as OG


class TestWhichStrategiesRun:

    def test_unset_is_btc5m_alone(self, monkeypatch):
        monkeypatch.delenv(F.FLEET_ENV, raising=False)
        assert F.fleet() == ["BTC5M"]

    def test_the_four_assets(self, monkeypatch):
        monkeypatch.setenv(F.FLEET_ENV, "BTC5M,ETH5M,SOL5M,XRP5M")
        assert F.fleet() == ["BTC5M", "ETH5M", "SOL5M", "XRP5M"]

    @pytest.mark.parametrize("raw", ["btc5m, eth5m", " BTC5M ,ETH5M,"])
    def test_case_and_spacing(self, monkeypatch, raw):
        monkeypatch.setenv(F.FLEET_ENV, raw)
        assert F.fleet() == ["BTC5M", "ETH5M"]

    def test_an_unregistered_strategy_refuses(self, monkeypatch):
        monkeypatch.setenv(F.FLEET_ENV, "BTC5M,DOGE5M")
        with pytest.raises(SystemExit, match="not a .*registered strategy"):
            F.fleet()

    def test_a_fifteen_minute_strategy_refuses(self, monkeypatch):
        """This fleet is the FIVE-minute one. Admitting BTC15M would put a
        b5m-prefixed process on the 15-minute lane's series."""
        monkeypatch.setenv(F.FLEET_ENV, "BTC5M,BTC15M")
        with pytest.raises(SystemExit, match="FIVE-MINUTE"):
            F.fleet()

    def test_a_repeated_strategy_refuses(self, monkeypatch):
        """Two children on one series would share a ledger and a coid
        prefix and interleave their orders."""
        monkeypatch.setenv(F.FLEET_ENV, "BTC5M,BTC5M")
        with pytest.raises(SystemExit, match="twice"):
            F.fleet()

    def test_set_but_empty_refuses_rather_than_defaulting(self, monkeypatch):
        monkeypatch.setenv(F.FLEET_ENV, " , ")
        with pytest.raises(SystemExit, match="names nothing"):
            F.fleet()


class TestEachChildsStateIsItsOwn:

    FOUR = ["BTC5M", "ETH5M", "SOL5M", "XRP5M"]

    def test_ledgers_and_strategies_are_distinct(self):
        led = {s: F.child_env(s, "/state", 4)["L5_BTC5M_LEDGER"] for s in self.FOUR}
        aut = {s: F.child_env(s, "/state", 4)["L5_LIVE_AUTH_LEDGER"] for s in self.FOUR}
        assert len(set(led.values())) == 4 and len(set(aut.values())) == 4
        assert led["ETH5M"] == "/state/eth/ledger.json"
        assert aut["XRP5M"] == "/state/xrp/auth.jsonl"
        for s in self.FOUR:
            assert F.child_env(s, "/state", 4)["L5_5M_STRATEGY"] == s

    def test_the_distinctness_check_catches_a_collision(self, monkeypatch):
        """Pins the GUARD, by making two rows resolve to one asset dir."""
        monkeypatch.setitem(F.REG.BY_STRATEGY, "ETH5M",
                            F.REG.BY_STRATEGY["ETH5M"]._replace(asset="btc"))
        with pytest.raises(SystemExit, match="would share"):
            F._distinct(["BTC5M", "ETH5M"], "/state")

    def test_a_child_never_forks_its_own_fleet(self, monkeypatch):
        monkeypatch.setenv(F.FLEET_ENV, "BTC5M,ETH5M")
        assert F.FLEET_ENV not in F.child_env("ETH5M", "/state", 4)

    def test_each_asset_carries_its_own_owner_approval(self, monkeypatch):
        """A fleet-wide approval names ONE series. Giving it to all four
        would either refuse three of them or, if the series check ever
        softened, authorize four assets from one owner decision."""
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", "{btc-approval}")
        monkeypatch.setenv(F.APPROVAL_PREFIX + "ETH5M", "{eth-approval}")
        assert F.child_env("ETH5M", "/state", 4)["L5_CAMPAIGN_APPROVAL"] \
            == "{eth-approval}"
        # BTC keeps the existing variable, so today's deployment is unchanged
        assert F.child_env("BTC5M", "/state", 4)["L5_CAMPAIGN_APPROVAL"] \
            == "{btc-approval}"

    def test_an_asset_with_no_approval_gets_an_empty_one(self, monkeypatch):
        """It must NOT inherit BTC's. An empty approval refuses at the
        gate, which is the safe direction."""
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", "{btc-approval}")
        monkeypatch.delenv(F.APPROVAL_PREFIX + "SOL5M", raising=False)
        assert F.child_env("SOL5M", "/state", 4)["L5_CAMPAIGN_APPROVAL"] == ""

    def test_coid_prefixes_stay_distinct_across_the_fleet(self):
        coids = [OG.STRATEGY_COID_PREFIX[s] for s in self.FOUR]
        assert len(set(coids)) == 4
        F._distinct(self.FOUR, "/state")        # does not raise


class TestAccountLanesAreDeclaredByTheFleet:
    """Each lane checks its own ledger against cap/lanes. The fleet is the
    only thing that knows how many children there are, so it tells them."""

    def test_every_child_is_told_the_same_lane_count(self):
        envs = [F.child_env(s, "/state", 4) for s in
                ("BTC5M", "ETH5M", "SOL5M", "XRP5M")]
        assert {e["L5_ACCOUNT_LANES"] for e in envs} == {"4"}

    def test_a_one_asset_fleet_declares_one_lane(self):
        assert F.child_env("BTC5M", "/state", 1)["L5_ACCOUNT_LANES"] == "1"

    def test_the_count_overrides_an_inherited_value(self, monkeypatch):
        """A stale variable on the service must not out-vote the fleet's
        own child count -- that is how lanes come to disagree."""
        monkeypatch.setenv("L5_ACCOUNT_LANES", "1")
        assert F.child_env("ETH5M", "/state", 4)["L5_ACCOUNT_LANES"] == "4"

    def test_the_lane_count_is_required_not_defaulted(self):
        """One lane is the permissive answer; it must not be reachable by
        forgetting the argument."""
        with pytest.raises(TypeError):
            F.child_env("BTC5M", "/state")
