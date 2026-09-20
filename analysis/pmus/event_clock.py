"""Expected remaining event duration at a point in time. OUTSIDE Model A.

This exists because the residual comparison in 86cda1fa showed the transfer risk
is residual x SETTLEMENT CLOCK, and L5's clock was 5-15 minutes while a PMUS
sports clock is the length of the game. A probe envelope needs to bound
time-to-expected-settlement at the moment of a first fill, so it has to be
measurable.

It is an ESTIMATE from sport format and elapsed time since kickoff, wall-clock
inclusive of stoppages/intervals. It is never a venue guarantee, nothing in the
frozen policy reads it, and it is not fitted to any outcome.
"""
import json, os, collections

SCR = os.path.dirname(os.path.abspath(__file__))

# wall-clock minutes from scheduled start to expected resolution, typical
SPORT_WALL_MIN = {
    "table-tennis": 45, "esports": 75, "tennis": 135, "soccer": 115,
    "basketball": 130, "ice-hockey": 150, "football": 200, "baseball": 180,
    "mma": 60, "boxing": 60, "efootball": 30, "darts": 90,
    "pickleball": 60, "lacrosse": 130, "volleyball": 100, "cricket": 480,
}
LEAGUE_OVERRIDE = {"nfl": 200, "cfb": 215}


def league_sport():
    try:
        return {k: v["sport"] for k, v in
                json.load(open(os.path.join(SCR, "pmus_leagues_sports.json"))).items()}
    except OSError:
        return {}


_LS = league_sport()


def wall_minutes(league):
    if league in LEAGUE_OVERRIDE:
        return LEAGUE_OVERRIDE[league]
    return SPORT_WALL_MIN.get(_LS.get(league))


def remaining_s(league, kickoff_epoch, now_epoch):
    """Expected seconds from `now` to resolution, or None when unknown."""
    w = wall_minutes(league)
    if w is None or kickoff_epoch is None:
        return None
    return max(0.0, kickoff_epoch + w * 60.0 - now_epoch)
