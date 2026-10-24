"""Replay the five stranded-leg recovery actions over the real 0230z/0300z tapes."""
import json
from decimal import Decimal as D

SP = "/tmp/claude-0/-home-user-kalshi-arb-bot/e2b83131-1eb4-52c2-b749-df2a8ca69b61/scratchpad"
TICK = D("0.01")
TAKER_THETA = D("0.0695")
MAKER_THETA = D("0.0125")        # credit magnitude
TERMINAL = 600.0                 # binding terminal, window seconds
ARM_LO, ARM_HI = 540.0, 585.0    # existing policy's actionable interval
MAKER_CEIL = D("0.96")
DEF_CEIL = D("1.20")

def fee(theta, p):
    return theta * p * (D(1) - p)

class Pos:
    """A stranded first leg."""
    def __init__(self, held, qty, cost, t_fill):
        self.held, self.qty, self.cost, self.t_fill = held, D(qty), D(cost), t_fill

def agg_complement_own(held, bid, ask):
    """Own price of the complement when acquired by CROSSING. One wire order:
    buying the complement of a held leg IS selling that leg."""
    return D(ask) if held == "DOWN" else D(1) - D(bid)

def maker_complement_own(held, bid, ask, k):
    """Own price of the complement resting k ticks behind the touch, and the
    UP-price level the tape must reach for it to fill."""
    if held == "DOWN":                      # complement is UP: rest a BID
        m = D(bid) - k * TICK
        return m, m, "down"                 # fills if UP falls to <= m
    m_wire = D(ask) + k * TICK              # complement is DOWN: rest an ASK
    return D(1) - m_wire, m_wire, "up"      # fills if UP rises to >= m_wire

def realized_complete(pos, m):
    """Both sides held -> guaranteed $1. Outcome-independent."""
    return pos.qty * (D(1) - pos.cost - m)

def realized_hold(pos, up_won):
    won = (up_won if pos.held == "UP" else not up_won)
    return pos.qty * ((D(1) if won else D(0)) - pos.cost)

def p_held_wins(pos, bid, ask):
    mid = (D(bid) + D(ask)) / 2
    return mid if pos.held == "UP" else D(1) - mid

def replay(name, tape, pos, up_won, t_cutoff=TERMINAL):
    pts = [r for r in tape if pos.t_fill <= r["t"] <= t_cutoff]
    print(f"\n{'='*78}\n{name}: held {pos.held} q={pos.qty} cost={pos.cost} "
          f"filled T+{pos.t_fill:.0f}   settlement: UP {'WON' if up_won else 'LOST'}")
    print(f"{'='*78}")
    hold_real = realized_hold(pos, up_won)
    print(f"  ACTION 5  HOLD TO SETTLEMENT          realized {hold_real:+.4f}")
    print(f"\n  {'T+':>5} {'book':>11} {'agg m':>7} {'pair':>6} {'fee':>7} "
          f"{'A3/A4 realized':>15} {'EV(exit)':>9} {'EV(hold)':>9} {'arm?':>5}")
    best = None
    for r in pts:
        bid, ask = r["bid"], r["ask"]
        m = agg_complement_own(pos.held, bid, ask)
        pair = pos.cost + m
        f = fee(TAKER_THETA, m) * pos.qty
        real = realized_complete(pos, m) - f
        ph = p_held_wins(pos, bid, ask)
        ev_hold = pos.qty * ph - pos.qty * pos.cost
        ev_exit = realized_complete(pos, m) - f
        armed = ARM_LO <= r["t"] < ARM_HI
        if best is None or real > best[1]:
            best = (r["t"], real, pair, m)
        print(f"  {r['t']:>5.0f} {bid}/{ask} {m:>7} {pair:>6} {f:>7.4f} "
              f"{real:>+15.4f} {ev_exit:>+9.4f} {ev_hold:>+9.4f} "
              f"{'YES' if armed else '-':>5}")
    print(f"\n  best aggressive-completion moment: T+{best[0]:.0f} "
          f"realized {best[1]:+.4f} at pair {best[2]} (complement own {best[3]})")
    return hold_real, best, pts

tapes = {w: json.load(open(f"{SP}/tape_{w}.json")) for w in ("0230z", "0300z")}

# 0230z: DOWN leg filled at own 0.07 at 02:34:48 = T+288. UP ended 0.98/0.99.
r1 = replay("0230z", tapes["0230z"], Pos("DOWN", "1.17", "0.07", 288.0), up_won=True)
# 0300z: UP leg filled at own 0.20 at 03:05:31 = T+331. UP collapsed to 0.01.
r2 = replay("0300z", tapes["0300z"], Pos("UP", "1.79", "0.20", 331.0), up_won=False)
