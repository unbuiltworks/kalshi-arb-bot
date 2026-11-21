# l5-native-shadow

L5's engine, on L5's own markets, 24/7, read-only.

- **Service** `l5-native-shadow` (`27d029d8-e6e6-4410-8052-24fa748856a8`)
- **Runs** `python3 -m src.l5_native_shadow`, 30s cycle
- **Universe** every open `btc-updown-15m-*` and `eth-updown-15m-*` window
- **Model** frozen `bbb55bf90638d2bd`, read never retrained
- **Credentials** none; unauthenticated GETs only
- **Order path** none in the image; a test parses the AST and fails on any
  `post` / `delete` / `create_order` call

## Reading the output

`L5_NATIVE_SHADOW` once per cycle: markets seen, `would_act`,
`primary_blockers`, `all_blockers_seen`.

`L5_NATIVE_ROW` for any market that would act or errored.

`primary_blocker` is the first in precedence — money, then venue, then
fidelity. `all_blockers` is the complete set, because reporting only the
primary let the daily stop hide the capital finding underneath it.

## What it cannot do

Place an order. Change a cap. Reset the daily stop. Retrain the model.
