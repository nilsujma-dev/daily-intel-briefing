# Credentials go here

Everything in this directory is git-ignored except this file. Nothing here is ever
committed. Create these three files by hand — they are the only manual setup.

```
secrets/
├── client_secret.json    Google OAuth client (you download this)
├── token.json            created automatically by ./run.sh --authorize
└── anthropic_api_key     your Anthropic API key, one line, no quotes
```

## 1. `client_secret.json` — Google OAuth client

Sending uses the Gmail API with a single scope, `gmail.send`. The app can send mail
as you and **cannot read your mailbox**.

1. Create a project at <https://console.cloud.google.com/>
2. **APIs & Services → Library** → enable **Gmail API**
3. **APIs & Services → OAuth consent screen**
   - **Internal** if you are on Google Workspace — strongly preferred, because
     *External* projects stuck in "Testing" expire refresh tokens after 7 days and
     your scheduled job goes quiet after a week
   - **External** on a personal Gmail account; publish the app to avoid that expiry
4. **Credentials → Create credentials → OAuth client ID → Desktop app**
5. Download the JSON and save it as `secrets/client_secret.json`

## 2. `token.json` — created for you

```bash
./run.sh --authorize
```

Opens a browser once. The refresh token it stores renews itself indefinitely.

A headless server has no browser, so authorise on a desktop first and copy the file
across — `deploy.sh` does this automatically.

## 3. `anthropic_api_key` — for Claude prioritisation

Create a key at <https://console.anthropic.com/settings/keys>:

```bash
printf '%s' 'sk-ant-...' > secrets/anthropic_api_key
chmod 600 secrets/anthropic_api_key
```

`ANTHROPIC_API_KEY` in the environment works too and takes precedence.

**Optional.** Without a key the briefing still sends every morning, ranked by the
built-in keyword scoring instead of by Claude.

## Permissions

```bash
chmod 700 secrets && chmod 600 secrets/*
```

## If you ever leak one

- **Google OAuth client** — delete the client in Cloud Console, create a new Desktop
  client, replace the file, re-run `--authorize`
- **Anthropic key** — revoke at console.anthropic.com and write the new one here
- Revoke app access at <https://myaccount.google.com/permissions>
