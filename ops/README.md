# Emergency halt marker

This branch holds one thing: the place where the operator publishes a
signed emergency-halt file, `ops/EMERGENCY_HALT.json`.

- The independent watchdog reads that file every 20 seconds.
- A file with a valid signature latches the halt: the trading bot refuses
  every order and the watchdog cancels every resting order.
- A file can only halt. It can never start or resume trading.
- Deleting the file does not undo a halt. Only the owner's reset
  procedure does.

How to sign and publish a file: `analysis/l5/sports/OPERATOR_SETUP_GUIDE.md`
on the research branch. Never commit the signing secret here or anywhere.

No file present = no halt requested.
