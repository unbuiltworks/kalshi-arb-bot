"""Expected-settlement horizon classifier. OUTSIDE Model A.

This is a DEPLOYMENT/OPPORTUNITY constraint, not a strategy rule and not a
model input. Nothing here is consulted by the frozen policy, no threshold is
fitted, and long-horizon markets are never removed from research statistics --
they are reported separately.

WHY NOT THE VENUE'S endDate: it is a backstop expiry, not expected settlement.
aec-atp-norgom-stesak-2026-09-20 -- a tennis match played on the 20th -- carries
endDate 2026-10-04. Using it would classify nearly every match market as long
horizon. Expected settlement is therefore taken from the SPORT/LEAGUE FORMAT,
which is what actually determines when a live event finishes.
"""
import json, os

SCR = os.path.dirname(os.path.abspath(__file__))

# formats that finish within a day of going live
SHORT_SPORTS = {"tennis", "table-tennis", "esports", "soccer", "football",
                "basketball", "ice-hockey", "baseball", "mma", "boxing",
                "efootball", "darts", "pickleball", "lacrosse", "volleyball"}
# multi-day or open-ended formats
LONG_SPORTS = {"golf", "motorsports", "misc", "espnocho", "cycling"}
# cricket splits by format, not by sport
CRICKET_SHORT = {"odicr", "odiwcr", "odcup", "ipl", "iplcr", "cpl", "cplcr",
                 "mlccr", "etpl", "t20i", "t20icr", "t20iw", "t20iwcr",
                 "t20blast", "t20blastcr", "t20blastw", "t20blastwcr",
                 "t20gsl", "t20worldcupw", "t20worldcupwcr", "hundred",
                 "hundredw"}
CRICKET_LONG = {"county", "testcr"}


def league_sport():
    try:
        return {k: v["sport"] for k, v in
                json.load(open(os.path.join(SCR, "pmus_leagues_sports.json"))).items()}
    except OSError:
        return {}


_LS = league_sport()


# OWNER 2026-09-20: cricket is RESEARCH-ONLY regardless of format. ODI and T20
# settle inside a day, so this is not a horizon judgement and the horizon()
# classification below still reports their true format. It is a separate
# deployment exclusion, applied on top, and it exists so that the 0.52
# Zimbabwe/Australia ODI episode cannot influence deployable economics.
# Cricket data stays in the research corpus in full.
CRICKET_LEAGUES = CRICKET_SHORT | CRICKET_LONG


def is_cricket(league):
    return league in CRICKET_LEAGUES or _LS.get(league) == "cricket"


def horizon(league):
    """'SHORT' (<=24h expected settlement), 'LONG', or 'UNKNOWN'.

    This reports FORMAT DURATION only. It is deliberately not the deployment
    test -- see deployable() -- so cricket keeps its honest horizon in research
    statistics.
    """
    if not league:
        return "UNKNOWN"
    if league in CRICKET_SHORT:
        return "SHORT"
    if league in CRICKET_LONG:
        return "LONG"
    sp = _LS.get(league)
    if sp == "cricket":
        return "UNKNOWN"          # format not established -- never assumed short
    if sp in SHORT_SPORTS:
        return "SHORT"
    if sp in LONG_SPORTS:
        return "LONG"
    return "UNKNOWN"


def deployable(league):
    """Promotion candidate: expected settlement <=24h AND not cricket."""
    return horizon(league) == "SHORT" and not is_cricket(league)


def exclusion_reason(league):
    """Why a league is not a promotion candidate, or None if it is."""
    if is_cricket(league):
        return "CRICKET_RESEARCH_ONLY"
    h = horizon(league)
    if h == "LONG":
        return "SETTLEMENT_OVER_24H"
    if h == "UNKNOWN":
        return "HORIZON_UNKNOWN"
    return None
