# FanDuel vs Kalshi MLB Home Run shadow — 2026-08-20

Exact game/start/player/threshold shadow comparison for KXMLBHR against FanDuel batter_home_runs_alternate. Includes conservative Kalshi q=1 taker fees and depth. Pregame settlement remains non-executable because FanDuel requires a plate appearance while Kalshi requires starting lineup + plate appearance, with lifecycle equivalence still incomplete.

Regression gate: 4/4 passing after switching Kalshi team identity to the event-ticker decoder. Launching one-shot full-slate shadow scan across current Kalshi HR games using a Railway reference to the existing Odds API credential. No alerts or order entry.
