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
        led = {s: F.child_env(s, "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_BTC5M_LEDGER"] for s in self.FOUR}
        aut = {s: F.child_env(s, "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_LIVE_AUTH_LEDGER"] for s in self.FOUR}
        assert len(set(led.values())) == 4 and len(set(aut.values())) == 4
        assert led["ETH5M"] == "/state/eth/ledger.json"
        assert aut["XRP5M"] == "/state/xrp/auth.jsonl"
        for s in self.FOUR:
            assert F.child_env(s, "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_5M_STRATEGY"] == s

    def test_the_distinctness_check_catches_a_collision(self, monkeypatch):
        """Pins the GUARD, by making two rows resolve to one asset dir."""
        monkeypatch.setitem(F.REG.BY_STRATEGY, "ETH5M",
                            F.REG.BY_STRATEGY["ETH5M"]._replace(asset="btc"))
        with pytest.raises(SystemExit, match="would share"):
            F._distinct(["BTC5M", "ETH5M"], "/state")

    def test_a_child_never_forks_its_own_fleet(self, monkeypatch):
        monkeypatch.setenv(F.FLEET_ENV, "BTC5M,ETH5M")
        assert F.FLEET_ENV not in F.child_env("ETH5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])

    def test_each_asset_carries_its_own_owner_approval(self, monkeypatch):
        """A fleet-wide approval names ONE series. Giving it to all four
        would either refuse three of them or, if the series check ever
        softened, authorize four assets from one owner decision."""
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", "{btc-approval}")
        monkeypatch.setenv(F.APPROVAL_PREFIX + "ETH5M", "{eth-approval}")
        assert F.child_env("ETH5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_CAMPAIGN_APPROVAL"] \
            == "{eth-approval}"
        # BTC keeps the existing variable, so today's deployment is unchanged
        assert F.child_env("BTC5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_CAMPAIGN_APPROVAL"] \
            == "{btc-approval}"

    def test_an_asset_with_no_approval_gets_an_empty_one(self, monkeypatch):
        """It must NOT inherit BTC's. An empty approval refuses at the
        gate, which is the safe direction."""
        monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", "{btc-approval}")
        monkeypatch.delenv(F.APPROVAL_PREFIX + "SOL5M", raising=False)
        assert F.child_env("SOL5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_CAMPAIGN_APPROVAL"] == ""

    def test_coid_prefixes_stay_distinct_across_the_fleet(self):
        coids = [OG.STRATEGY_COID_PREFIX[s] for s in self.FOUR]
        assert len(set(coids)) == 4
        F._distinct(self.FOUR, "/state")        # does not raise


class TestAccountLanesAreDeclaredByTheFleet:
    """Each lane checks its own ledger against cap/lanes. The fleet is the
    only thing that knows how many children there are, so it tells them."""

    def test_every_child_is_told_the_same_lane_count(self):
        envs = [F.child_env(s, "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"]) for s in
                ("BTC5M", "ETH5M", "SOL5M", "XRP5M")]
        assert {e["L5_ACCOUNT_LANES"] for e in envs} == {"4"}

    def test_a_one_asset_fleet_declares_one_lane(self):
        assert F.child_env("BTC5M", "/state", ["BTC5M"])["L5_ACCOUNT_LANES"] == "1"

    def test_the_count_overrides_an_inherited_value(self, monkeypatch):
        """A stale variable on the service must not out-vote the fleet's
        own child count -- that is how lanes come to disagree."""
        monkeypatch.setenv("L5_ACCOUNT_LANES", "1")
        assert F.child_env("ETH5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_ACCOUNT_LANES"] == "4"

    def test_the_lane_count_is_required_not_defaulted(self):
        """One lane is the permissive answer; it must not be reachable by
        forgetting the argument."""
        with pytest.raises(TypeError):
            F.child_env("BTC5M", "/state")


class TestExplicitLedgerPaths:
    """A lane already trading has settled rows under the path it was
    deployed with. Moving it to a derived path resets `day_settled_pnl`
    to 0 -- readable-but-empty answers 0, not None -- so the day-loss
    guard fails OPEN. These overrides keep the running lane where it is."""

    LED, AUT = "/state/btc5m/btc5m_ledger.json", "/state/btc5m/auth.jsonl"

    def test_an_explicit_path_wins_over_the_derived_one(self, monkeypatch):
        monkeypatch.setenv(F.LEDGER_PREFIX + "BTC5M", self.LED)
        monkeypatch.setenv(F.AUTH_PREFIX + "BTC5M", self.AUT)
        e = F.child_env("BTC5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])
        assert e["L5_BTC5M_LEDGER"] == self.LED
        assert e["L5_LIVE_AUTH_LEDGER"] == self.AUT

    def test_it_only_moves_the_lane_it_names(self, monkeypatch):
        monkeypatch.setenv(F.LEDGER_PREFIX + "BTC5M", self.LED)
        e = F.child_env("ETH5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])
        assert e["L5_BTC5M_LEDGER"] == "/state/eth/ledger.json"

    def test_unset_and_blank_keep_the_derived_path(self, monkeypatch):
        monkeypatch.setenv(F.LEDGER_PREFIX + "BTC5M", "   ")
        assert F.child_env("BTC5M", "/state", self.FOUR if hasattr(self,"FOUR") else ["BTC5M","ETH5M","SOL5M","XRP5M"])["L5_BTC5M_LEDGER"] \
            == "/state/btc/ledger.json"

    def test_an_override_cannot_make_two_lanes_share_a_ledger(self, monkeypatch):
        """The collision check runs on the paths actually handed out."""
        monkeypatch.setenv(F.LEDGER_PREFIX + "ETH5M", "/state/btc/ledger.json")
        with pytest.raises(SystemExit) as exc:
            F._distinct(["BTC5M", "ETH5M"], "/state")
        assert "share" in str(exc.value)

    def test_an_override_colliding_on_the_auth_ledger_is_refused(self, monkeypatch):
        monkeypatch.setenv(F.AUTH_PREFIX + "SOL5M", "/state/xrp/auth.jsonl")
        with pytest.raises(SystemExit) as exc:
            F._distinct(["SOL5M", "XRP5M"], "/state")
        assert "share" in str(exc.value)


class TestEachLaneBindsItsOwnPort:
    """OBSERVED 2026-11-16T11:56Z: all four children inherited one
    L5_BTC5M_FILLS_PORT, the first bound it, the rest died with
    OSError(98), and the fleet's fail-closed rule then stopped every
    lane. A port is shared state exactly as a ledger path is."""

    FOUR = ["BTC5M", "ETH5M", "SOL5M", "XRP5M"]

    def test_each_lane_gets_a_distinct_port(self, monkeypatch):
        monkeypatch.setenv(F.FILLS_PORT_ENV, "8082")
        got = [F.child_env(s, "/state", self.FOUR)[F.FILLS_PORT_ENV]
               for s in self.FOUR]
        assert got == ["8082", "8083", "8084", "8085"]
        assert len(set(got)) == 4

    def test_the_first_lane_keeps_the_configured_port(self, monkeypatch):
        """So a one-lane fleet is reachable where it always was."""
        monkeypatch.setenv(F.FILLS_PORT_ENV, "8082")
        assert F.child_env("BTC5M", "/state", ["BTC5M"])[F.FILLS_PORT_ENV] \
            == "8082"

    def test_the_port_follows_position_not_name(self, monkeypatch):
        monkeypatch.setenv(F.FILLS_PORT_ENV, "9000")
        order = ["ETH5M", "BTC5M"]
        assert F.child_env("ETH5M", "/state", order)[F.FILLS_PORT_ENV] == "9000"
        assert F.child_env("BTC5M", "/state", order)[F.FILLS_PORT_ENV] == "9001"

    def test_unset_falls_back_to_the_documented_default(self, monkeypatch):
        monkeypatch.delenv(F.FILLS_PORT_ENV, raising=False)
        assert F.fills_port_base() == F.DEFAULT_FILLS_PORT

    @pytest.mark.parametrize("raw", ["", "80", "99999", "eighty", "8082.0"])
    def test_an_unusable_port_refuses_or_defaults_but_never_guesses(
            self, monkeypatch, raw):
        monkeypatch.setenv(F.FILLS_PORT_ENV, raw)
        if raw == "":
            assert F.fills_port_base() == F.DEFAULT_FILLS_PORT
        else:
            with pytest.raises(SystemExit):
                F.fills_port_base()

    def test_distinct_refuses_a_collision_before_any_child_starts(
            self, monkeypatch):
        """Belt and braces: if a future change made two lanes collide,
        it is a refusal with a reason rather than a startup race."""
        monkeypatch.setenv(F.FILLS_PORT_ENV, "8082")
        monkeypatch.setattr(F, "fills_port_base", lambda: 8082)
        monkeypatch.setattr(
            F, "child_env",
            lambda s, root, children: {"L5_BTC5M_LEDGER": "/l/%s" % s,
                                       "L5_LIVE_AUTH_LEDGER": "/a/%s" % s,
                                       F.FILLS_PORT_ENV: "8082"})
        with pytest.raises(SystemExit) as exc:
            F._distinct(self.FOUR, "/state")
        assert "fills port" in str(exc.value)
