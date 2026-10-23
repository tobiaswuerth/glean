# glean: imports what your sources hold into orbit and sift

[![CI](https://github.com/tobiaswuerth/glean/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/tobiaswuerth/glean/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/glean-importer)](https://pypi.org/project/glean-importer/)
[![Python](https://img.shields.io/pypi/pyversions/glean-importer?logo=python&logoColor=white)](https://pypi.org/project/glean-importer/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/tobiaswuerth/glean/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/tobiaswuerth/glean)](https://github.com/tobiaswuerth/glean/blob/main/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

glean reads information from your sources and records each item in two local stores:
[orbit](https://github.com/tobiaswuerth/orbit), a personal CRM that holds the structured view (people, organizations,
interactions, tasks, topics), and [sift](https://github.com/tobiaswuerth/sift), a local search that holds the full
text. Email works today: from an IMAP mailbox such as Outlook.com or Hotmail, signed in with OAuth2, or from a folder
of saved `.eml` files.

For every item, glean writes a Markdown document, then starts a fresh [Claude Code](https://claude.com/claude-code)
agent run whose only tools are orbit's and sift's MCP tools. The agent decides who is who, logs the interaction,
updates people, organizations and tasks, and makes the item searchable. Neither orbit nor sift knows glean exists.

- **One run per item, oldest first.** orbit grows as your mailbox did: the people and tasks an earlier email created
  exist when a later one mentions them, answers them or settles them. Each run starts with a fresh context, so it
  knows only what orbit and sift hold. Several runs go at once where emails share no people, company or thread.
- **The source is never changed.** Mail folders are opened read-only and messages fetched without marking them read;
  what glean processed is recorded only in its own databases, which can be inspected and reset.
- **A sandbox that is checked.** The importing agent has no shell, file or web tools, and none of orbit's
  destructive ones (merge, rename). Every run's first event lists the tools it really has; a run with anything else
  is stopped before it acts. Items are treated as data, never as instructions
  ([how](https://github.com/tobiaswuerth/glean/blob/main/docs/how-it-works.md#the-agent-run)).
- **Spam and mass mail stay out, judged by the agent.** What Outlook.com's filters and the headers say (spam and bulk
  ratings, mailing-list headers, no-reply senders, whether you wrote to the sender) goes to the agent as evidence, not
  as rules, since rules skipped people too; it skips automated mail and spam without a tool call, for about $0.002
  with Haiku. Only senders you list are skipped without a run.
- **Runs that leave work undone are sent back.** glean checks each import's tool calls: the interaction logged,
  every unknown person and organization taken up, sift asked to index, no failed call dropped. What is left goes back
  to the agent in the same session, up to twice, and into the report. A batch whose runs keep going wrong stops.
- **A second look by a stronger model.** After a run, one Opus review per 100 imports reads what they wrote: it merges
  proven duplicates, corrects misplaced tasks and fields, sends wrongly judged emails back to be imported again, and
  suggests changes to the instructions (about $1 per 100 emails).
- **Questions only you can answer.** The review asks a few (who is this to you? is this your bank?) as notes in
  orbit's review queue. You type the answer into its column in Obsidian, whenever suits you; the next review records
  what you said and acts on it, and later imports find it as context.
- **sift kept in line.** glean adds the sift collections of its archive and of orbit's vault when none holds them,
  keeps the originals out of them, and has sift index every downloaded email.
- **Local.** The archive, glean's databases and the sign-in tokens stay on your machine; the tokens are encrypted
  with your Windows login (on macOS and Linux, kept in a file only you may read).

## Sources

| Source | Commands | Reads |
|---|---|---|
| Email | `glean mail …` | An IMAP mailbox with OAuth2 (Outlook.com, Hotmail: every folder but junk, trash and drafts), or a folder of `.eml` files |

## Requirements

- Python 3.11 or newer.
- [Claude Code](https://claude.com/claude-code) 2.1.290 or newer, signed in (`claude auth status`). On Windows, the
  native installer's `claude.exe`.
- [orbit](https://github.com/tobiaswuerth/orbit) 0.7.1 or newer and [sift](https://github.com/tobiaswuerth/sift) 1.3.1
  or newer (1.3.0 doesn't index the attachments the agent gives it), each serving MCP over HTTP: `orbit serve <vault>`
  (port 8775) and `sift serve` (port 8765). Give orbit's vault your addresses when you create it, with
  `orbit init <vault> --email you@example.com`.
- For an Outlook.com or Hotmail mailbox: an app registered in Microsoft Entra (free, about five minutes; step 1
  below), and IMAP turned on in Outlook's settings.

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install glean-importer   # installs the `glean` command
glean --version
```

Or with pip into a virtual environment: `pip install glean-importer`. The package is called `glean-importer`; the
command is `glean`. To update: `uv tool upgrade glean-importer`.

## Set up

### 1. Register glean with Microsoft (Outlook.com, Hotmail)

Outlook.com and Hotmail accept only OAuth2 for IMAP: Microsoft turned off passwords and app passwords for IMAP on
personal accounts in September 2024. A program that signs in with OAuth2 needs a client ID, which an app registered in
Microsoft Entra gives it. The app gives no one but you access: glean still signs in as you, in your browser.

1. Open the [Microsoft Entra admin center](https://entra.microsoft.com) and sign in with your Microsoft account. If it
   says your account has no directory, create a free Azure account first, which makes one.
2. **Applications > App registrations > New registration.** Name: `glean`. Supported account types: *Personal
   Microsoft accounts only*. Redirect URI: platform *Public client/native (mobile & desktop)*, `http://localhost`.
   **Register.**
3. **API permissions > Add a permission > Microsoft Graph > Delegated permissions:** `IMAP.AccessAsUser.All` and
   `offline_access`. **Add permissions.**
4. On the app's **Overview**, copy the **Application (client) ID**. It is not a secret: a public client has none.
5. In [Outlook on the web](https://outlook.live.com): **Settings > Mail > Forwarding and IMAP > Let devices and apps
   use IMAP:** on.

For a folder of `.eml` files instead of a mailbox, skip this step and set `[mail] eml_folder` after step 3.
[docs/microsoft-app.md](https://github.com/tobiaswuerth/glean/blob/main/docs/microsoft-app.md) has the same steps
with troubleshooting.

### 2. Choose glean's home

The folder for glean's configuration (`glean.toml`), its sign-in tokens, the mail it downloads (`mails/` and
`mails.db`) and what the agent's runs make of it (`runs/` and `runs.db`). It is `~/.glean` unless `GLEAN_HOME` names
another. Keep it out of synced folders: it holds your mail. (In a clone of this repository, `data/` is ignored by git
for this purpose.)

```powershell
setx GLEAN_HOME D:\glean-data   # Windows, optional; open a new terminal afterwards
```

### 3. Run the setup

```bash
glean setup
```

It asks for your address, the client ID and your other addresses, writes `glean.toml`, and opens the browser to sign
you in (`glean mail login` does that again later; `--device-code` signs in without a browser). It also starts by
itself the first time you use glean. The tokens are kept in glean's home, encrypted with your Windows login (on
macOS and Linux, in a file only you may read).

### 4. Check everything

```bash
glean doctor
```

It checks Claude Code and its sign-in, that orbit and sift answer, that an agent run gets exactly the allowed tools,
the mail sign-in, and which sift collections hold the archive and orbit's vault. Every line should start with `ok`.

sift's collections need nothing from you: with the first download, glean adds a collection for its archive (`mails`)
and one for orbit's vault (`crm`), unless one holds them already, and keeps the original `message.eml` files out with
a `.siftignore`, since sift reads each email's Markdown document. It finds the vault by asking orbit.

## Use

```bash
glean mail download            # fetch what is new; the first time, everything
glean mail list --oldest 20    # the 20 oldest not yet imported, in the order run takes them
glean mail run --oldest 20     # import them, oldest first, one agent run each, then review what they wrote
glean mail review --latest 50  # review the 50 latest imports again
glean mail show <id>           # one email: its state, files, and the agent's report
glean mail ignore <id>         # never import it
glean mail reset <id>          # import it again
glean status                   # counts per source and status, the agents' cost so far
```

`run` also takes `--latest N` for the newest instead (still oldest first among them; a later `--oldest` then imports
the older ones after the newer, so tasks they open and settle may come out of order), ids, `--parallel N` to keep N
agents busy (emails that share people, a company or a thread go one after the other where others can run meanwhile),
`--retry-failed` for emails whose run failed, `--include-skipped`
for those skipped before, and `--dry-run` to write the documents and show what the agent would get, without running
it. Each run ends `done`, `skipped` (the agent found it automated mail or spam, and says why) or `failed`.

**To import everything again**, delete `runs.db` and `runs/` in glean's home: every email is then as it was
downloaded, and nothing is downloaded again. For a fresh vault too, make a new one with `orbit init`.

**Try it on a test vault first.** Point `[orbit] url` at a copy of your vault (`orbit serve D:\crm-test --port 8776`),
import a few emails, read the reports (`glean mail show <id>`) and the notes, and only then use the real vault. In a
clone of this repository, [`experiments/`](https://github.com/tobiaswuerth/glean/blob/main/experiments/README.md) imports a made-up mailbox into a new vault with
the real agent and prints each run's report.

## Configuration

`glean.toml` in glean's home. The setup writes what only you can say (your address, the client ID) and shows the
rest commented out: glean's own choices fit most mailboxes, and the lists you give add to glean's, which improve with
each version. The settings:

| Setting | Default | |
|---|---|---|
| `[agent] model` | `haiku` | the model of each run: `haiku`, `sonnet`, `opus` or a full model id. In runs of 100 real emails each, Haiku cost $0.27 and Sonnet $4.08 |
| `[agent] parallel` | 1 | agent runs at once; `run --parallel` for one run |
| `[agent] review_every`, `review_model` | 100, `opus` | after a run, one review of every this many imports, by this model; 0: none, `run --no-review` for one run |
| `[agent] max_turns`, `timeout_minutes`, `max_budget_usd` | 60, 5, 1.0 | limits per item; a run that hits one fails, and is retried with `--retry-failed` |
| `[orbit] url` | `…:8775/mcp/write` | orbit's tools without merges, renames and lint |
| `[orbit] vault` | asked from orbit | orbit's vault, for its sift collection |
| `[sift] url` | `…:8765/mcp` | sift's MCP server |
| `[sift] crm_collection`, `document_collections` | `crm`, all | the vault's collection, and where to look for documents an item refers to |
| `[sift] add_collections` | on | add and keep the collections of the archive and the vault |
| `[mail] address`, `client_id` | | the account, and the Entra app's client ID |
| `[mail] my_addresses` | | your other addresses: mail from them is outbound |
| `[mail] folders` | all | read only these folders |
| `[mail] exclude_folders` | junk, trash, drafts, outbox, notes | more folders to leave out |
| `[mail] skip_senders` | none | senders whose mail is skipped without an agent run, such as `bot@example.com` or `*@alerts.example` |
| `[mail] archive`, `collection` | `mails` | where mail is kept (its database beside it, `mails.db`), and its sift collection |
| `[mail] save_attachments` | `.pdf`, `.docx`, `.xlsx`, `.pptx`, … | attachments saved beside the email for sift |
| `[mail] eml_folder` | | read `.eml` files from this folder instead of the IMAP server |

## How it works

[docs/how-it-works.md](https://github.com/tobiaswuerth/glean/blob/main/docs/how-it-works.md) describes the pipeline, the archive and its Markdown documents, glean's
databases, the agent's sandbox and how it is checked, and how a source is added.

## Security and privacy

Items are untrusted input: an email can carry text written to steer an agent. glean limits what such text can do (no
dangerous tools, no destructive ones, every run's tools checked, uncertainty turned into a question for you in orbit's
review queue) and keeps everything local. See [SECURITY.md](https://github.com/tobiaswuerth/glean/blob/main/SECURITY.md).

## Contributing

Bug reports and ideas are welcome as [issues](https://github.com/tobiaswuerth/glean/issues);
[CONTRIBUTING.md](https://github.com/tobiaswuerth/glean/blob/main/CONTRIBUTING.md) describes the setup for development, the code layout and how a release is made.
The [changelog](https://github.com/tobiaswuerth/glean/blob/main/CHANGELOG.md) lists what changed.

## License

[MIT](https://github.com/tobiaswuerth/glean/blob/main/LICENSE)
