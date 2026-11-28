# Operator setup: the emergency halt (two secrets)

For the owner. About 15 minutes. Do this after the watchdog release
`b79ad4a8b` is deployed (scheduled 04:30 UTC, 24 Nov). Nothing here places
an order or arms trading. Never paste the secret or the token into chat,
GitHub, a document or a screenshot.

## What you are setting up

A file you can publish to stop all trading within about a minute, from
anywhere, without a deployment. The watchdog reads it from our private
GitHub repository every 20 seconds. It can only stop trading; it can never
start it.

You will create two secret values and set them. Everything else is set
for you.

## Step 1 — Create the halt signing secret (2 minutes)

This is a long random password. Whoever holds it can halt trading.

Any one of these works:

- **A password manager** (1Password, Bitwarden, Apple Passwords): generate
  a random password of 64 characters, letters and digits only, and save it
  as "L5 halt signing secret".
- **Mac or Linux terminal:** `openssl rand -hex 32`
- **Any computer with Python:**
  `python3 -c "import secrets; print(secrets.token_hex(32))"`

Keep it in your password manager. You need it again in Step 5 and whenever
you sign a halt file.

Do not reuse this value for anything else. In particular it must be
different from the campaign signing secret you will create later for the
12-hour campaigns.

## Step 2 — Create the read-only GitHub token (5 minutes)

This lets the watchdog read one private file. It cannot change anything.

1. Sign in to github.com with an account that can see
   `unbuiltworks/kalshi-arb-bot`.
2. Click your picture (top right) → **Settings**.
3. Left column, at the bottom: **Developer settings**.
4. **Personal access tokens** → **Fine-grained tokens** → **Generate new
   token**.
5. Fill in:
   - **Token name:** `l5-halt-watchdog-read`
   - **Resource owner:** `unbuiltworks`
   - **Expiration:** 90 days (put a reminder in your calendar; when it
     expires the halt file can no longer be read, and you make a new one)
   - **Repository access:** *Only select repositories* →
     `kalshi-arb-bot`
   - **Permissions → Repository permissions → Contents:** *Read-only*
     (GitHub adds "Metadata: Read-only" by itself). Leave everything else
     at "No access".
6. **Generate token**. Copy it now (it starts with `github_pat_`); GitHub
   shows it once. Save it in your password manager as "L5 halt watchdog
   read token".

If "Resource owner" does not offer `unbuiltworks`, or the token shows
"pending approval": the organisation has to allow fine-grained tokens. As
an owner of the organisation: organisation **Settings** → **Personal
access tokens** → allow fine-grained tokens / approve the request.

## Step 3 — Set your two secrets in Railway (3 minutes)

The fixed, non-secret settings (the file's address, the content type, the
service name, the probe file and the channel timeout) are set for you by
the session, on your approval. You set only the two secrets.

1. Open the Railway project and click the service **`l5-halt-watchdog`**.
   Check the name: not the sports bot, not the ledger.
2. Open the **Variables** tab → **New Variable**, and add these two.

| Name | Value |
|---|---|
| `L5_EMERGENCY_HALT_SECRET` | the secret from Step 1 |
| `L5_EMERGENCY_HALT_BEARER` | the token from Step 2 |

3. Railway shows the changes as pending. Click **Deploy** (or **Apply
   changes**) so the watchdog restarts with them. It is back within a
   minute or two.
4. Tell this session "the two secrets are set". It will read the
   watchdog's start-up line and confirm the halt file is configured with a
   read credential, and that the channel reads healthy. It cannot see the
   values themselves, only that they are set.

**Once the secrets are set the channel is watched.** If the watchdog
cannot read the halt location for two minutes (wrong or expired token,
GitHub unreachable), it stops confirming "no halt" and the bot stops
opening new exposure about two minutes after that, pulling its resting
opening bids. Nothing is latched; it recovers by itself when the read
works again. So a typo in the token shows up within minutes as "channel
down", not as a silent gap.

## Step 4 — Prove it can read the file (no order, nothing halts)

This uses a file signed with a deliberately WRONG secret. The watchdog
must reject it by name. That proves it is reading the file.

1. On a computer with Python 3 and a copy of the repository, on the branch
   `release/l5-halt-watchdog-b79ad4a8b`, run:

   ```
   L5_EMERGENCY_HALT_SECRET=wrong-on-purpose python3 -m scripts.l5_emergency_halt_sign --service pmus-spring-l5-probe --reason "read test" > EMERGENCY_HALT.json
   ```

   If you would rather not run this yourself, ask this session to produce
   the wrong-secret file. It can do that safely, because it does not use
   the real secret.
2. Publish it: on github.com open the repository, switch to the branch
   `ops/halt-marker`, open the folder `ops`, **Add file → Create new
   file**, name it `EMERGENCY_HALT.json`, paste the file's contents,
   **Commit changes** directly to `ops/halt-marker`.
   (This session will create the `ops/halt-marker` branch for you when you
   say so; it holds nothing but this folder.)
3. Tell this session. Within about 20 seconds the watchdog should log
   that the signature is invalid and halt nothing.
4. Delete the file again (open it on GitHub → the bin icon → commit).

## Step 5 — How you halt trading for real

1. Sign a file with the REAL secret, on your own computer:

   ```
   L5_EMERGENCY_HALT_SECRET='<the secret from Step 1>' python3 -m scripts.l5_emergency_halt_sign --service pmus-spring-l5-probe --reason "why you are stopping" > EMERGENCY_HALT.json
   ```

2. Publish it exactly as in Step 4.2.

Within about a minute the watchdog latches the halt, the bot refuses every
order, and the watchdog cancels every resting order and checks each one at
the venue.

Things to know:

- **A signed file works once and for 24 hours.** For a 12-hour campaign,
  sign one at the start and keep it in your password manager; publishing
  it later is then only the paste-and-commit in Step 4.2, which you can do
  from a phone.
- **It cannot be undone by deleting the file.** After a halt, trading
  resumes only through the reset procedure, which needs your explicit
  approval each time.
- **If GitHub is unreachable the file cannot be read,** and after about
  four minutes the bot stops opening new exposure by itself and pulls its
  opening bids, until the read works again. The bot's own stops and the
  watchdog's own rules act throughout.
- **The token expires after 90 days.** When it does, the channel reads
  "down" and the bot stops opening exposure. Renew it before then.

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| This session reports the halt file is "not configured" | The secret is missing or its name is misspelt | Check the names in Step 3 exactly, and that you clicked Deploy |
| "Channel down" after you set the secrets | The watchdog cannot read the halt location: token wrong, expired, or without access to this repository | Recreate the token (Step 2) and set it again. Until it reads, the bot will not open new exposure |
| The wrong-secret test logs nothing | The watchdog cannot read the file | Check the token (not expired, Contents: Read-only, this repository), the URL, and that the file is on the branch `ops/halt-marker` in the folder `ops` |
| The real file logs "signature invalid" | It was signed with a different secret from the one in Railway | Sign again with the Step 1 secret |
| "wrong service" | The file was signed for another service name | Sign with `--service pmus-spring-l5-probe` |
| "stale" | The file is more than 24 hours old | Sign a new one |
| "already acted" | That file was used before | Sign a new one |

## Later, for the 12-hour campaigns (not now)

The sports bot needs its own three values, set the same way on the service
`pmus-spring-l5-probe`: a **different** signing secret for campaign files,
a read-only token (the Step 2 token can be reused, or make a second one),
and the campaign file's URL. A separate short guide will give the exact
names and values when that release is ready to deploy.
