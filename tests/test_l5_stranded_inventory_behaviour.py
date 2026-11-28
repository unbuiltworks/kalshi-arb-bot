"""What L5 demonstrably does with stranded inventory, pinned.

Source: `analysis/l5/residual_exit/forensics.py` (38,180 BTC15m residual
episodes, 5,546 windows, 693,916 fills, zero sells, 2026-03-19 -> 08-31) and
`analysis/l5/residual_exit/inventory_outcomes.py` (the window-level outcome
test this adds). TRAIN/OOS is CHRONOLOGICAL, never shuffled: 2026-03-19..08-10
against 08-10..08-31.

These are conditional rates on observational data, not an experiment. They say
where behaviour and money went, never what would happen if we changed ours.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------- reconciled
WINDOW_MATCHED_PNL = 162256.93
WINDOW_RESIDUAL_PNL = -144065.00
WINDOW_NET = 18191.93
N_EPISODES, N_WINDOWS = 38180, 5546
TRAIN_WINDOWS, OOS_WINDOWS = 3882, 1664


class TestTheLedgerReconciles:

    def test_window_net_is_matched_plus_residual(self):
        assert round(WINDOW_MATCHED_PNL + WINDOW_RESIDUAL_PNL, 2) == WINDOW_NET

    def test_carrying_residual_is_his_largest_single_loss_line(self):
        """-$144,065 against +$162,257 of matched: carrying stranded legs to
        settlement consumed 88.8% of everything the pairs earned."""
        assert abs(WINDOW_RESIDUAL_PNL) / WINDOW_MATCHED_PNL > 0.88
        assert WINDOW_NET > 0          # he was still net positive overall


class TestTheFourObservedTransitions:
    """BTC15m has zero sells, so inventory only ever grows and the vocabulary
    is exactly OPEN / EXTEND / COMPLETE / SWITCH. `NONE_CARRIED` (343, 0.9%)
    means only that NO FILL FOLLOWED -- it is NOT a 'wait'. An unfilled resting
    quote leaves no trace in this data, so "L5 waited" and "L5 quoted and was
    not hit" are the same observation and neither is claimed."""

    SHARES = {"EXTEND": 0.662, "SWITCH": 0.183, "COMPLETE_PARTIAL": 0.139,
              "NONE_CARRIED": 0.009, "COMPLETE_FLAT": 0.008}

    def test_extend_is_two_thirds_of_everything(self):
        assert self.SHARES["EXTEND"] == pytest.approx(0.662, abs=0.001)

    def test_the_shares_are_a_partition(self):
        assert sum(self.SHARES.values()) == pytest.approx(1.0, abs=0.002)

    def test_flattening_is_the_rarest_thing_he_does(self):
        assert self.SHARES["COMPLETE_FLAT"] < 0.01
        assert self.SHARES["COMPLETE_FLAT"] < self.SHARES["EXTEND"] / 50

    def test_none_carried_is_not_counted_as_a_wait(self):
        assert self.SHARES["NONE_CARRIED"] < 0.01


class TestTheOnlyBrakeThatReplicates:
    """ABSOLUTE RESIDUAL SIZE. As |residual| grows, extend falls and
    complement rises -- monotone, large, and the ordering holds out of
    sample. It is also the only replicating feature about a quantity WE can
    control, rather than about his queue position or fill rate."""

    #: bucket -> (train_extend, oos_extend, train_complement, oos_complement)
    RESIDUAL = {
        "<=1":    (0.689, 0.749, 0.004, 0.004),
        "1-5":    (0.709, 0.740, 0.023, 0.040),
        "5-20":   (0.662, 0.698, 0.119, 0.127),
        "20-100": (0.621, 0.606, 0.251, 0.280),
    }

    def test_extend_falls_as_the_residual_grows(self):
        order = ["1-5", "5-20", "20-100"]
        for i in range(len(order) - 1):
            assert self.RESIDUAL[order[i]][0] > self.RESIDUAL[order[i + 1]][0]
            assert self.RESIDUAL[order[i]][1] > self.RESIDUAL[order[i + 1]][1]

    def test_complement_rises_as_the_residual_grows(self):
        order = ["<=1", "1-5", "5-20", "20-100"]
        for i in range(len(order) - 1):
            assert self.RESIDUAL[order[i]][2] < self.RESIDUAL[order[i + 1]][2]
            assert self.RESIDUAL[order[i]][3] < self.RESIDUAL[order[i + 1]][3]

    def test_the_complement_rate_spans_an_order_of_magnitude(self):
        assert self.RESIDUAL["20-100"][3] / self.RESIDUAL["1-5"][3] > 6

    def test_run_length_also_replicates(self):
        """He continues what he is doing, more so the longer he has."""
        train = {1: 0.650, 2: 0.606, 3: 0.653, 4: 0.719}
        oos = {1: 0.648, 2: 0.581, 3: 0.656, 4: 0.733}
        assert train[4] == max(train.values())
        assert oos[4] == max(oos.values())

    def test_price_movement_predicts_by_magnitude_not_sign(self):
        """Symmetric: extend is highest at BOTH extremes and lowest flat."""
        train = {"flat": 0.611, "down": 0.653, "up": 0.655}
        oos = {"flat": 0.622, "down": 0.663, "up": 0.672}
        for d in (train, oos):
            assert d["flat"] == min(d.values())

    def test_whether_the_residual_is_favored_changes_nothing(self):
        assert abs(0.662 - 0.659) < 0.01      # train against vs favored
        assert abs(0.668 - 0.661) < 0.01      # oos


class TestAddingToAnOverweightSideIsMostlyTheOpeningFill:
    """THE PREMISE CORRECTED. P(extend) looks much higher when the book is
    lopsided -- 0.745 train / 0.761 oos at residual_rel >= 0.50 against 0.643 /
    0.652 below it. But 94.9% of those episodes hold ONLY ONE SIDE, with a
    median of 1 fill so far, 1.9% of the window elapsed and ZERO matched
    inventory. It is the window's opening fill, not a double-down on a large
    adverse position."""

    OVERWEIGHT = {"train": (4522, 0.7452), "oos": (1325, 0.7608)}
    NOT_OVERWEIGHT = {"train": (22204, 0.6433), "oos": (10129, 0.6519)}
    ONE_SIDED_SHARE = 5548 / 5847
    #: rel >= 0.50 AND a complement already held: the genuine case
    GENUINE = {"train": (217, 0.535, 0.272), "oos": (84, 0.488, 0.440)}

    def test_the_apparent_effect_replicates(self):
        for s in ("train", "oos"):
            assert self.OVERWEIGHT[s][1] > self.NOT_OVERWEIGHT[s][1] + 0.09

    def test_but_it_is_almost_entirely_one_sided_opening_state(self):
        assert self.ONE_SIDED_SHARE > 0.94

    def test_in_the_genuine_case_he_does_not_lean_in(self):
        """With a complement already held he extends about half the time and
        complements far more than the headline suggests."""
        for s in ("train", "oos"):
            n, ext, comp = self.GENUINE[s]
            assert ext < 0.55
            assert comp > self.NOT_OVERWEIGHT[s][1] / 3

    def test_the_genuine_case_is_small_and_must_be_reported_as_such(self):
        assert self.GENUINE["train"][0] + self.GENUINE["oos"][0] == 301


class TestNoBehaviourPredictsRealizedOutcomeOutOfSample:
    """THE NEGATIVE RESULT, and the reason none of this is adopted. The same
    features that predict his ACTION with Spearman 1.00 predict his window NET
    at between -1.0 and +0.5, and every OOS spread EXCEEDS its TRAIN spread --
    the signature of noise, not signal."""

    #: feature -> (rho(train,oos) on window net, train_spread, oos_spread)
    NET = {
        "max_run_len":             (0.4, 4.8357, 29.2325),
        "median_episode_dur_s":   (-0.6, 4.8016, 27.0773),
        "complement_share":        (0.1, 8.4680, 22.4096),
        "overweight_extend_share":(-1.0, 9.1072, 21.4777),
        "extend_share":            (0.1, 10.2109, 21.2321),
        "switch_share":            (0.4, 11.0359, 19.5174),
    }

    def test_no_feature_reaches_a_usable_rank_correlation(self):
        assert max(r for r, _, _ in self.NET.values()) <= 0.5

    def test_every_oos_spread_exceeds_its_train_spread(self):
        for f, (_, tr, oos) in self.NET.items():
            assert oos > tr, f

    def test_the_overweight_feature_inverts_perfectly(self):
        assert self.NET["overweight_extend_share"][0] == -1.0

    def test_the_window_is_the_only_honest_unit(self):
        """An EXTEND books no matched P&L and a COMPLETE does, so per-episode
        net is mechanically biased toward completing; the extend's value lands
        in a later episode of the same window."""
        assert WINDOW_NET == round(WINDOW_MATCHED_PNL + WINDOW_RESIDUAL_PNL, 2)


class TestHisOwnEdgeDecayedInsideTheCorpus:
    """Context for every figure above. 98% of his BTC15m net was earned in the
    first 70% of the period; in the last three weeks he made $354.70 on 328,629
    fills."""

    TRAIN = {"n": 3882, "sum": 17837.22, "fills": 365287, "per_fill": 0.04883}
    OOS = {"n": 1664, "sum": 354.70, "fills": 328629, "per_fill": 0.00108}

    def test_net_per_fill_fell_by_more_than_fortyfold(self):
        assert self.TRAIN["per_fill"] / self.OOS["per_fill"] > 40

    def test_almost_all_of_it_was_earned_before_the_holdout(self):
        assert self.TRAIN["sum"] / (self.TRAIN["sum"] + self.OOS["sum"]) > 0.98

    def test_the_fill_count_barely_fell_while_the_money_did(self):
        """Not a volume effect: he kept trading and stopped earning."""
        assert self.OOS["fills"] / self.TRAIN["fills"] > 0.85

    def test_btc15m_was_never_the_480_a_day_target(self):
        days = 144
        assert self.TRAIN["sum"] / days < 130


class TestAtOurSizeHisBehaviourIsTheOppositeOfOurEconomics:
    """THE TRANSLATION RESULT. Our campaign residuals -- 1.17, 1.79, 3.36
    shares -- sit at the 5.7th to 15.3rd percentile of his. In that bucket his
    demonstrated behaviour is EXTEND 0.713 and COMPLEMENT 0.027: he adds, and
    essentially never pairs. He can afford that because he has 32.48 fills per
    window and will reach 20-100 shares where he complements 28% of the time.
    We get 4.52 fills and never reach that regime, so his small-residual
    behaviour is the opening phase of a trajectory we cannot complete."""

    PCTL = {1.17: 5.7, 1.79: 8.4, 3.36: 15.3}
    SMALL_BUCKET = {"n": 6459, "extend": 0.713, "complement": 0.027}
    FILLS_PER_WINDOW = {"l5": 32.48, "ours": 4.52}

    def test_our_sizes_are_in_his_bottom_sixth(self):
        assert max(self.PCTL.values()) < 16

    def test_his_behaviour_there_is_to_add_not_to_pair(self):
        assert self.SMALL_BUCKET["extend"] > 0.70
        assert self.SMALL_BUCKET["complement"] < 0.03

    def test_we_cannot_reach_the_regime_where_he_pairs(self):
        assert self.FILLS_PER_WINDOW["l5"] / self.FILLS_PER_WINDOW["ours"] > 7

    def test_so_copying_it_reproduces_the_loss_line_not_the_edge(self):
        """Extending a residual we cannot pair is exactly what produced our
        0300z -$0.3580 and his -$144,065."""
        assert WINDOW_RESIDUAL_PNL < 0
        assert self.SMALL_BUCKET["complement"] < 0.03
