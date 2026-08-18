# Exact pair audit gate

Before any cross-exchange pair can become executable, it must pass a structured identity audit: exact participants, threshold, game start time, and settlement semantics. Date-only matching is not sufficient because teams can play on consecutive days or doubleheaders.

`src/pair_audit.py` is read-only and prints the source fields used for that audit.
