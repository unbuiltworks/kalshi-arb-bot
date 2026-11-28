# 0x8dxd replication analysis, 2026-11-18

Read-only. Every number below is reproduced by the scripts in this
directory from `data/l5/competitor_0x8dxd/` (untracked, 236MB + 583MB,
preserved on disk, ignored because three files exceed GitHub's 100MB
per-file limit). Wallet `0x63ce342161250d705dc0b16df89036c8e5f9ba9a`,
name `0x8dxd`, 2026-03-23 .. 2026-08-25.

Run them from the repository root, e.g. `PYTHONPATH=. python3
analysis/l5/competitor_0x8dxd/replay.py`.

## 1. The competitor P&L is NOT usable as realized profit

`gap.py` attributes the reported $30,573.84 discrepancy exactly:

    total (net basis, 3,991 conditions)   $122,002.39
    buy-only decomposition reported        $91,428.55
    GAP                                    $30,573.84
      (a) 1,517 conditions omitted         $50,443.44
      (b) sell proceeds ignored by basis   -$19,869.60

Two incommensurable things had been differenced: a total over 3,991
conditions net of sells, against a decomposition over 3,025 conditions on
a buy-only basis.

`recon.py` then ties a complete decomposition to $-0.00 -- but at a
DIFFERENT total, $181,801.62, because the two runs treat net-short sides
differently. That single accounting choice swings the answer by
$59,799.23, which is larger than the entire supposed edge.

WHY: the tape is incomplete.

  - 644 of 3,991 conditions (16.1%) show SELLS EXCEEDING BUYS on a side.
    That is impossible without buys the tape does not contain.
  - 2,939 of 6,930 conditions (42.4%) have no resolution row. $2,920,936
    was paid into them and $202,480 came back; the outcome is UNKNOWN and
    is excluded rather than counted.
  - NO `MERGE` activity type is captured at all. Merging a matched pair
    back to $1 is the main settlement path for a pair trader, so the
    settlement leg is simply absent.
  - No per-trade fees on trade rows.

CONCLUSION: neither $122,002.39 nor $181,801.62 is realized profit. Both
are withdrawn and neither should be carried forward.

## 2. What IS reliable: directly credited rows, no reconstruction

From `activity.jsonl` (`w1.py`), venue-credited over the whole history:

    REFERRAL_REWARD   $49,435.87   (103 rows)  <- NOT replicable by copying trades
    MAKER_REBATE      $ 3,967.96   ( 13 rows)  <- we earn $0 at Tier 1
    TAKER_REBATE      $   122.25   ( 10 rows)
    REWARD            $   583.13   (  2 rows)
    ------------------------------------------
    total             $54,109.21

These need no reconstruction and survive every caveat above. Any target
inherited from "$480/day" should have this stripped out before we measure
ourselves against it.

## 3. Observed behaviour (`behav.py`, 55,884 in-window buys)

                      p10      p50      p90
    time in window     9s      63s     189s
    price           0.300    0.490    0.680
    size (shares)    18.9     78.6     99.1
    median clip notional  $35.00

    after 270s   1.2%     above 0.90   0.5%     at/below 0.10   1.5%
    windows 4,015, of which one-sided only 23.8%
    EXTEND 48,811 : COMPLETE 3,058  = 16:1

Three differences against our controller, in order of economic weight:

  1. ENTRY PRICE LOCUS. Their median entry is 0.490. Our fixed straddle
     rung was 0.08, where only 1.5% of their volume sits, and our
     late-favourite band floor is 0.51 -- just above their median. In
     fixed mode neither strategy quotes where they actually trade.
     Book-relative mode already quotes at the touch, which is the
     existing, tested answer to this.
  2. SAME-SIDE ACCUMULATION DEPTH. 16 EXTENDs per COMPLETE, and 23.8% of
     windows are never completed at all. Our live unmatched ceilings
     ($0.10-$0.30) permit roughly one clip before refusing.
  3. CLIP SIZE. $35.00 median against our $0.05-$0.30.

(2) and (3) are owner risk settings, not defects. They are reported as
measurements, not as recommendations.

## 4. Controller replay (`replay.py`, 300 windows, 2,681 decision points)

    0x8dxd actual : EXTEND 2,796  SELL/FLIP 767  OPEN 302  COMPLETE 245
    our controller: HOLD_OR_SKIP 2,678  OPEN_FAVORITE 3
    refusals      : PAIR_ABOVE_CEILING 2,099 | STRADDLE_RESTING 442
                    NO_EXPOSURE_ROOM 103 | ENTRY_WINDOW_CLOSED 35

`sens.py`: pair ceiling 0.96 -> 2 placements, 0.98 -> 3, and 1.00 is
rejected by validation (`ZERO < ceiling < ONE`, because a pair at par
cannot profit). The ceiling is therefore NOT the swing factor, and
raising it buys one placement in 2,681.

### The load-bearing caveat

NO recorded book exists for these windows (Mar-Aug 2026). The replay's
bid/ask is a PRINT PROXY: each side's last observed print. That assumes
they traded at the touch. If they rested below it, the true pair cost
differs and `PAIR_ABOVE_CEILING` is a fact about the proxy rather than
about our policy. Queue position and executable depth are NOT modelled,
and fills are UNOBSERVABLE throughout. `NO_EXPOSURE_ROOM` is partly an
artifact of feeding their 78-share inventory into our $5 cap.

So "we would refuse 99.9% of their decision points" is NOT established as
a policy finding and is not claimed as one.

## 5. What would make this decidable

Every open policy question -- pair ceiling, clip size, extend depth --
turns on a book we do not have for the competitor's windows. The missing
input is recorded books alongside our own quoting, which
`l5-btc5m-public-recorder` already produces (see
`data/l5/phase_a/btc5m_books.jsonl`, 105 authenticated snapshots on the
integration branch). Until that exists for windows we also trade, the
comparison cannot be made against an observed touch.

NO controller change is proposed on this evidence.
