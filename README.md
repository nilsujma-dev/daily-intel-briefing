# Daily Intel Briefing

A self-contained daily briefing on AI and cyber security news, delivered as one email
each morning. Ten RSS feeds in, two prioritised tables out — ranked by Claude for a
director-level reader rather than sorted by timestamp.

**No dependencies.** Pure Python standard library. No `pip install`, no virtualenv, no
Docker. If `python3` runs, this runs.

---

## What it does

Every morning at 08:00 it fetches ten feeds, discards anything it has sent before,
collapses stories that several outlets covered into one row, ranks what is left, and
emails you a single message containing two tables:

- **Enterprise Cyber Security Intelligence Briefing**
- **AI News Briefing**

Each table has five columns: **Priority · Source · Short Information · Additional
Details · Link**.

Priority is `P1 act today`, `P2 review this week`, `P3 monitor`.

```
┌──────────┬──────────────────┬────────────────────────────────┬─────────────────────────┬────────┐
│ P1       │ CISA Advisories  │ Emergency Directive over       │ CVE-2026-21893, CVSS    │ Open › │
│ Act today│ 27 Jul, 06:31    │ actively exploited Ivanti      │ 9.8, unauthenticated    │        │
│          │                  │ Connect Secure zero-day        │ RCE. Agencies must      │        │
│          │                  │ Also reported by Krebs, Bleep… │ patch within 48 hours.  │        │
└──────────┴──────────────────┴────────────────────────────────┴─────────────────────────┴────────┘
```

**[See a full rendered sample →](docs/sample-briefing.html)** (generated from the test
fixtures, so every story in it is fictional.)

Run it by hand any time with `./run.sh`, or let the schedule do it.

---

## Why it is built this way

Three problems make a daily news digest annoying, and most of the code addresses them.

**You get the same story five times.** Ten feeds covering one industry overlap heavily.
Naive URL matching does not help, because outlets rewrite headlines. See
[duplicate suppression](#duplicate-suppression).

**You get the same story again tomorrow.** Feeds re-publish, edit and re-date items. The
app permanently remembers everything it has emailed.

**Everything looks equally important.** A digest sorted by time buries an actively
exploited zero-day under a vendor press release. See [prioritisation](#prioritisation).

---

## Requirements

| | |
|---|---|
| Python | 3.8 or newer — the one already on macOS or Ubuntu is fine |
| Google account | Gmail or Workspace, for sending |
| Anthropic API key | Optional. Without it, ranking falls back to keyword scoring |
| Scheduling | macOS `launchd` or Linux `systemd`, both included |

On Debian and Ubuntu the standard library is split across packages. A minimal image may
lack `sqlite3` and `ca-certificates`; `deploy.sh` detects and installs them.

---

## Quick start

```bash
git clone https://github.com/YOUR-USERNAME/daily-intel-briefing.git
cd daily-intel-briefing

# 1. Where should the briefing go?
cp config/settings.local.example.json config/settings.local.json
$EDITOR config/settings.local.json          # set "recipient"

# 2. Credentials — full walkthrough in secrets/README.md
#    Put your Google OAuth client at secrets/client_secret.json, then:
./run.sh --authorize
./run.sh --test-email

# 3. Optional: let Claude do the prioritising
printf '%s' 'sk-ant-...' > secrets/anthropic_api_key
chmod 600 secrets/anthropic_api_key

# 4. See a real briefing without sending anything
./run.sh --check-feeds
./run.sh --dry-run --preview

# 5. Send it, then schedule it for 08:00 daily
./run.sh
./install-schedule.sh                        # macOS
```

**[`secrets/README.md`](secrets/README.md) is the credential guide** — where each file
goes, how to create it, and how to rotate it if leaked.

---

## Configuration

`config/settings.json` holds the defaults and is tracked in git.
`config/settings.local.json` holds your personal values, is git-ignored, and is
deep-merged over the defaults — so list only what you change:

```json
{
  "recipient": "you@yourdomain.com",
  "lookback_hours": 30,
  "max_items_per_table": 12,
  "curation": { "model": "claude-opus-5" }
}
```

Useful keys:

| Key | Default | Meaning |
|---|---|---|
| `recipient` | — | Where the briefing is sent. Required |
| `lookback_hours` | `30` | How far back a run looks. Stretches to the previous run automatically |
| `hard_max_age_hours` | `72` | Ceiling on that stretch |
| `max_items_per_table` | `12` | Keeps the email readable |
| `curation.enabled` | `true` | `false` disables Claude entirely; no API calls |
| `curation.model` | `claude-opus-5` | `claude-sonnet-5` is cheaper and nearly as good |
| `curation.rewrite_details` | `true` | `false` keeps the raw feed summaries |
| `scoring.business_strategy_weight` | `1.35` | Bias toward funding, M&A, regulation |

---

## The feeds

Edit `config/feeds.json` — flip `enabled` to add or drop a source. Nine extra vetted
feeds ship pre-configured and disabled.

**Cyber security**

| Source | Why |
|---|---|
| CISA Advisories | Authoritative advisories, KEV additions, emergency directives |
| Krebs on Security | Investigative breach and fraud reporting |
| BleepingComputer | Fastest breaking incident and ransomware coverage |
| The Hacker News | Broad daily threat coverage |
| Dark Reading | CISO and enterprise risk angle |

**AI**

| Source | Why |
|---|---|
| TechCrunch AI | Funding, M&A, product launches |
| VentureBeat AI | Enterprise adoption and vendor strategy |
| MIT Technology Review | Policy, regulation, deeper analysis |
| The Verge AI | Industry and big-tech moves |
| Ars Technica AI | Technically grounded reporting |

RSS 2.0, Atom and legacy RDF are all parsed, with conditional `GET` so unchanged feeds
cost nothing. One dead feed never blocks a briefing.

---

## Duplicate suppression

Three independent layers.

**1. Recency window.** Only items published within `lookback_hours` are considered. The
window stretches back to the last successful run, so a day the machine was off is still
covered, capped at `hard_max_age_hours`.

**2. Cross-source collapse.** The same story from several feeds becomes one row. This is
the interesting part: matching compares the *identifying facts* of a headline — vendor and
product names, CVE ids, monetary figures — rather than overall word overlap, because two
outlets describe one event in very different words:

> "CISA issues Emergency Directive over actively exploited Ivanti Connect Secure zero-day"
> "Ivanti Connect Secure zero-day exploited by suspected nation-state actors, CISA warns"

Those share almost no ordinary vocabulary but the same entities, so they merge. The most
authoritative source keeps the row; the others appear as *"Also reported by…"*.

A **conflicting entity vetoes any merge**, which is what keeps

> "CISA adds **Ivanti** flaw to KEV catalog"
> "CISA adds **Citrix** flaw to KEV catalog"

apart despite near-identical phrasing. Entities come from capitalisation, monetary amounts
are normalised so `$12.3M` and `$12.3 million` become one token, and a small alias table
handles `EU`/`Europe`.

**3. Permanent memory.** Every item ever emailed is recorded in `state/briefing.db` by
URL, GUID and normalised title, then re-checked fuzzily against the last ten days.
Nothing is sent twice, even if a feed re-publishes it with a new link or an edited
headline. History is written **only after the mail server confirms the send**, so a
delivery failure never silently swallows a day of news.

`tests/test_dedupe.py` pins this down with 21 cases — 10 pairs that must merge, 11 that
must stay apart.

---

## Prioritisation

Each morning the surviving stories go to **Claude**, which sets the priority, the running
order of both tables, and writes the *Additional Details* line.

It is prompted as the editor of a briefing for a director accountable for security
posture, technology strategy and budget — explicitly not a SOC analyst. It weighs how
widely deployed the affected technology is, whether exploitation is actually happening,
and whether the reader can realistically act. A vendor calling its own release "critical"
does not earn P1; a quietly worded advisory about software every enterprise runs might.

The details line is rewritten rather than passed through: one sentence, max 28 words,
leading with the concrete fact — the product, the CVE, the figure, the deadline — with
marketing language and `[...]` artifacts stripped.

Output is forced through a tool schema, so the response is either schema-valid or an
error. There is no "the model wrapped prose around the JSON" case to parse around.

### It degrades rather than failing

Keyword scoring always runs first — it selects which stories are worth an API call, and it
is the fallback. **Any** problem sends the briefing anyway, keyword-ranked, with the footer
saying so:

| Situation | Behaviour |
|---|---|
| No API key, or key revoked | Keyword ranking |
| Rate limit or network blip | One retry with backoff, then keyword ranking |
| `400` / `401` | No retry — fails fast |
| Claude omits a story | Keeps its keyword priority, appended in keyword order |
| Claude returns an unknown id | Discarded; nothing else affected |
| Invalid priority value | That story keeps its keyword priority |
| Malformed or prose-only response | Keyword ranking |

No failure mode can drop a story. A P1 budget of roughly the top third of each table is
enforced afterwards so the label keeps meaning on a heavy news day; it only ever demotes
the lowest-ranked excess. `tests/test_curation.py` covers this with 28 checks.

Cost is one call per morning over roughly 40 stories. The editorial instructions are the
`SYSTEM_PROMPT` at the top of `briefing/curate.py` — edit that if the judgement calls do
not match yours.

---

## Scheduling

**macOS**

```bash
./install-schedule.sh      # LaunchAgent at 08:00 daily
./uninstall-schedule.sh
```

Runs whether or not a terminal is open, and catches up if the Mac was asleep at 08:00.

**Linux** — see deployment below; `systemd` timer with `Persistent=true`.

---

## Deploying to a Linux server

From your workstation, not the server:

```bash
cp deploy.local.conf.example deploy.local.conf   # set HOST and USER_NAME
./deploy.sh
```

Idempotent — rerun after any change.

**It works on a bare server.** The only thing that must already exist is `sshd`, which
cannot be installed remotely. Everything else is probed and installed:

| Probe | Why it is not safe to assume |
|---|---|
| `python3` present | Absent on truly minimal images |
| `import sqlite3` | `python3-minimal` has no sqlite3, and the send-history needs it |
| `import ssl` | Same stdlib split |
| A real verified HTTPS request | `ssl` imports fine and still fails every request without `ca-certificates` — tested against a live endpoint rather than inferred |
| `timedatectl` timezone | Without `tzdata` the timer fires at the wrong hour |

Anything missing is installed with apt, then the whole set is **re-probed**, so an install
that silently did not fix the problem is caught before the script claims success.

It transfers the app, the credentials with permissions forced to `0600`, and
`state/briefing.db` — carrying the send-history across is what stops the server re-sending
everything you already read. Transfer is `tar` piped over ssh rather than `scp`, because
OpenSSH 9 implements `scp` on SFTP and a minimal `sshd` may not ship `sftp-server`.

It installs a **user** systemd timer — nothing in `/opt`, no system service, no root-owned
files. Sudo is used for at most two things, and the script detects which apply before
starting: the apt install, and `loginctl enable-linger` so the timer fires when you are not
logged in. Passwords are typed interactively; none is stored.

Before reporting success it starts the unit once — the only way to confirm the systemd
sandbox really permits SQLite to write to `state/`, rather than discovering it failed at
08:00 the next morning.

**It disables the macOS agent**, because two schedules means two briefings a day from two
diverging histories. `--keep-mac-schedule` overrides.

---

## Commands

| Command | What it does |
|---|---|
| `./run.sh` | Build and send today's briefing |
| `./run.sh --dry-run` | Build it, send nothing, record nothing |
| `./run.sh --preview` | Write the HTML to `state/preview/` |
| `./run.sh --diagnose` | Explain exactly why a briefing came out empty |
| `./run.sh --check-feeds` | Health-check every feed |
| `./run.sh --stats` | Run history, item counts, per-feed failure streaks |
| `./run.sh --since 72` | Widen the lookback window |
| `./run.sh --force` | Ignore the already-sent history |
| `./run.sh --authorize` | One-time Google sign-in |
| `./run.sh --test-email` | Send a short test message |
| `./scripts/check-secrets.sh` | Scan for credential material before pushing |

---

## Layout

```
briefing/fetch.py        RSS / Atom / RDF parsing, conditional GET, parallel fetch
briefing/normalize.py    URL canonicalisation, entity and money extraction
briefing/dedupe.py       three-layer duplicate suppression
briefing/score.py        keyword scoring — candidate selection and fallback ranking
briefing/curate.py       Claude prioritisation, ordering, details rewriting
briefing/render.py       HTML + plain-text email
briefing/gmail.py        OAuth 2.0 with PKCE, Gmail send
briefing/store.py        SQLite state
briefing/pipeline.py     orchestration
config/                  feeds and settings
deploy/                  systemd service + timer
tests/                   dedupe, curation and end-to-end suites
secrets/                 credentials (git-ignored)
state/                   database, logs, previews (git-ignored)
```

```bash
python3 tests/test_dedupe.py
python3 tests/test_curation.py
python3 tests/test_e2e.py        # full pipeline, network and Gmail stubbed
```

---

## Security

- Gmail scope is `gmail.send` only. The app cannot read your mailbox.
- Credentials live in `secrets/`, git-ignored, `chmod 600`.
- Personal configuration lives in `*.local.*` files, also git-ignored.
- `./scripts/check-secrets.sh --install` adds a pre-commit hook that blocks a commit
  containing key material. It inspects exactly the file set a commit would capture.
- The systemd unit runs unprivileged with `ProtectSystem=strict` and write access limited
  to `state/`.

If you ever leak a credential, **rotate it** — deleting the commit is not enough. See
[`secrets/README.md`](secrets/README.md).

---

## Troubleshooting

**Empty briefing.** Run `./run.sh --diagnose`. It prints newest and oldest item per feed,
a histogram of how many items fall within 6/12/24/30/48/72/168 hours, and the attrition at
each pipeline stage, then names the cause. Usually either everything was already sent
(correct behaviour) or the feeds are lagging and `lookback_hours` is too tight.

**`Not authorised yet`** → `./run.sh --authorize`.

**A feed shows `ERR`** → `./run.sh --stats` for its failure streak. One dead feed never
blocks a briefing; the footer marks it red.

**Google refresh token expires weekly** → the OAuth consent screen is *External* and stuck
in Testing. Switch to *Internal* on Workspace, or publish the app.

**Nothing arrives after a reboot (Linux)** → linger is off:
`loginctl enable-linger YOURUSER`.

---

## Licence

MIT — see [LICENSE](LICENSE).
