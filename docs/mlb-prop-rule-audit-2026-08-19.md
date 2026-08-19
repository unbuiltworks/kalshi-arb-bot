# MLB player-prop settlement audit — 2026-08-19

Scope: current Kalshi and Polymarket US MLB player-prop families used by the strict
cross-venue scanner.  This artifact documents the fail-closed machine policy; it
does not grant order-entry eligibility.

## Kalshi

Audited current series and contract terms:

- `KXMLBHIT` / `MLBHITS.pdf`
- `KXMLBHRR` / `MLBHITSRUNSRBIS.pdf`
- `KXMLBTB` / `MLBTOTALBASES.pdf`
- `KXMLBKS` / `MLBSTRIKEOUT.pdf`

Key semantics encoded in `src/mlb_prop_rule_certifications.py`:

- batter families require starting lineup plus at least one plate appearance;
- strikeout props require the named starting pitcher plus at least one batter faced;
- extra innings count;
- pre-official cancellation/postponement/suspension that does not resume within
  48 hours of the scheduled start resolves at last fair market price;
- an official shortened full-game result can govern settlement;
- source hierarchy begins with the governing league / official scorer and includes
  MLB.com and named secondary sources;
- official stats are final at game conclusion for contract purposes; later stat
  corrections do not change the contract.

## Polymarket US

Audited live instrument descriptions from the current MLB prop inventory and the
current Sports FAQ:

- batter props require the player to be in the starting lineup and record a plate
  appearance, otherwise LFMP;
- current strikeout instruments require the listed player to be the starting
  pitcher, otherwise LFMP;
- extra innings count;
- current live descriptions use a two-calendar-day rescheduling condition for
  delayed/postponed/suspended games;
- hits, H+R+RBI, and strikeout samples explicitly say an official shortened result
  governs; the sampled total-bases description did not state that clause;
- current live instruments identify MLB as the outcome source;
- the post-game stat-correction window is not explicit enough in the audited live
  instrument text / Sports FAQ to declare exact equivalence with Kalshi.

## Certification conclusion

Pregame Kalshi <-> Polymarket US player props remain **not certified for guaranteed
arbitrage**.  The normal-stat definitions and threshold semantics are close, but the
exceptional-event clock and correction tail are not proven identical.

Live participation can remove only the participation blocker.  It cannot erase the
remaining lifecycle/source/correction blockers.  An MLB `Final` state can be used by
the separate decided-prop research lane to identify stale known-winner quotes, but
those are labelled settlement-lag signals rather than cross-venue arbitrage.

No real venue pair was promoted to `FULL_RULES_AUDITED_EXECUTABLE` by this audit.
