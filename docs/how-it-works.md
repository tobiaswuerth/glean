# How glean works

```
glean <source> download    the source's new items → its archive (originals and documents) and the archive's
                           database; then sift indexes them. --oldest N / --latest N: only that many
glean <source> list        the items not yet imported, oldest first
glean <source> run         oldest first, several at a time where they share no people or thread:
                             1. document   the connector writes the item's Markdown, and files sift can read
                             2. import     a new `claude -p` process, whose only tools are orbit's and sift's
                             3. record     the outcome in runs.db, the agent's report and transcript in runs/
```

Everything that depends on the kind of source lives in a **connector** (`glean/connectors/<name>/`): how to set it
up, how to download from it, how to turn an item into Markdown, what it shares with other items, and what the agent
should do with such an item. Everything else is shared: the state, choosing and scheduling items, the agent run,
recording the outcome, sift's collections, and the commands.

## The home

What a download brings and what the agent's runs make of it are kept apart:

```
<GLEAN_HOME>/
├── glean.toml, tokens-mail.bin   the configuration and the mail sign-in
├── mails/                        the mail archive: what the downloads brought, sift's collection
├── mails.db                      its database: every email, where it was found, where the downloads left off
├── runs/                         what the runs wrote: reports and transcripts
└── runs.db                       what the runs made of each email, and every run
```

Deleting `runs.db` and `runs/` starts the imports afresh: every email is as it was downloaded (new, or skipped by your
`skip_senders`), with nothing downloaded again; what you decided with `ignore` or `reset` stays. orbit's vault and its
sift collection are orbit's: for a fresh start there too, make a new vault with `orbit init`.

## The archive

Each source has an archive (for mail, `<GLEAN_HOME>/mails` by default), with one folder per item:

```
mails/2026/10/14/3f2a9c1b7d4e/
├── message.eml                                       the original, as downloaded
├── 2026-10-14 0932 Offerte Wartungsvertrag 2027.md   the document: the agent's input, sift's document, orbit's source_file
└── Offerte Wartungsvertrag 2027.pdf                  attachments sift can read

runs/mails/2026/10/14/3f2a9c1b7d4e/
├── report.md                                         the last agent run's report, with its outcome, turns and cost
└── transcript.jsonl                                  every event of every run, as Claude Code wrote them
```

The folder's date is the item's, in local time; the folder's name is the item's id; its runs' folder in `runs/` has
the same path. File names are safe on Windows and made unique within the folder. Every downloaded email gets its
document at once, so that sift can search all mail, not only what the agent imported. The `.siftignore` glean
writes keeps the originals out, since sift would read each email twice: from its `message.eml` and from its document;
and, where a collection holds the whole home, `runs/` too.

### An email's document

Flat front matter, each value written as JSON (which YAML reads too, so no header can break it), then the text as it
came, quoted history and signatures included: telling them apart is the agent's job.

```markdown
---
type: "email"
message_id: "caf1234@mail.acme.example"
date: "2026-10-14T09:32:00+02:00"
folder: "Inbox"
folders: ["Archive", "Inbox"]
direction: "inbound"
from: "Hans Müller <hans.mueller@acme.example>"
to: ["Tobias Würth <tobias@example.com>"]
cc: ["Lea Frei <lea.frei@acme.example>"]
bcc: []
sender: null
reply_to: []
subject: "Offerte Wartungsvertrag 2027"
in_reply_to: null
references: []
attachments: ["Offerte 2027.pdf", "Foto.png"]
original: "message.eml"
sha256: "…"
---

Grüezi Herr Würth
…

## Attachments

- Offerte 2027.pdf (application/pdf, 182.3 KB, sha256 …): saved as [Offerte 2027.pdf](<Offerte 2027.pdf>)
- Foto.png (image/png, 2.1 MB, sha256 …): not saved
```

Parsing uses Python's `email` package with its modern policy: encoded headers decoded, addresses with display names,
the date with its offset. The body is the plain-text part; an email with only HTML is converted to text with
[inscriptis](https://github.com/weblyzard/inscriptis) (Apache-2.0), which keeps tables and lists readable. Images
shown in the body are left out; messages attached whole (forwarded as attachments) become sections of their own.
Message-IDs are normalized: lower case, without angle brackets; an email without one gets an id made from its digest.

## The state

Two kinds of SQLite database. The one beside each archive (`mails.db` beside `mails/`) records what the downloads
brought:

| Table | Holds |
|---|---|
| `items` | one row per item: key (an email's Message-ID), id, date, title, author, direction, the original's path and sha256, the document's path, and what the download, your `skip_senders` or you decided: `new`, `skipped` (with the pattern), `failed` (unreadable, or a server's notice in its place) or `ignored` |
| `locations` | every place an item was found: an email in Inbox and Archive is one item in two places |
| `cursors` | how far each folder is downloaded, where the next download starts: for IMAP, its UIDVALIDITY and last UID |
| `contacts` | the addresses the owner has written to |
| `marks` | what glean keeps for the source, such as the version its items were brought in line with |

`runs.db` in the home records what the runs made of them:

| Table | Holds |
|---|---|
| `outcomes` | per item: `done`, `failed` or `skipped` (the agent's verdict, a run's error), attempts, cost, the last report's path, and `wanted`: why you or a review asked for it to be imported, until a run imports or skips it |
| `runs` | every agent run: when, outcome, turns, cost, duration |

Items are keyed by what identifies them across places, not by a server's numbers: an email can sit in two folders,
and IMAP UIDs change when a folder's UIDVALIDITY does. An item's status is its runs' outcome, else what the archive's
database says, unless it is `ignored`: one of `new`, `done`, `failed`, `skipped` or `ignored`. *Not yet imported*
means `new`; `run --retry-failed` adds `failed`, `--include-skipped` adds `skipped`. `--oldest N` takes the N oldest
of them, so that orbit grows in the order things happened: a person or a task an email creates exists when a later one
answers or settles it. `--latest N` takes the newest instead; both run them oldest first. A failed item stays failed
until it is retried; a stopped run (Ctrl+C) leaves its item as it was. `reset` makes an item new again, keeping the
number of its runs, so that its next run knows it is a retry, and wanted: it is imported without a first look. Nothing
marks the source: not every server keeps custom flags, and the state here can be inspected and reset. A `runs.db` of
glean 0.12 gains `wanted` when glean 0.13 first opens it; an older glean then refuses it.

Items an older glean downloaded are brought in line with this version before the next download, run or list, once
per version: what its own rules skipped is new again, for the agent to judge, and what lacks a document gets one;
items an agent ran on keep their status. When the owner's addresses (`my_addresses`) change, the mail downloaded
before is brought in line once: what its original's From shows was sent from an address added is the owner's
(outbound), its recipients contacts and no skip rule setting it aside, and its document says so; what was sent from
one taken out is someone else's again. Only mail whose From names an owner's address, or that counted as theirs, is
read; a stop midway is made good the next time. A home of glean 0.3 or older, with everything in one `state.db`,
isn't read: glean says so, and a new home starts with a new download.

orbit is the second safety net: `log_interaction` returns the existing note for a Message-ID it knows, so an email
imported twice is still one interaction. A retry tells the agent so, and it completes what is missing.

## Reading mail

- **IMAP**, read-only: folders are opened with `EXAMINE`, messages fetched with `BODY.PEEK[]`; the commands sent are
  `AUTHENTICATE`, `LIST`, `EXAMINE`, `UID SEARCH`, `UID FETCH` and `LOGOUT` (the tests check this). Every folder is
  read except those the server marks as junk, trash or drafts and those in `exclude_folders`. A download fetches the
  Message-IDs of new messages first, so a copy of a known email isn't downloaded twice, then the messages in batches,
  and can be stopped and started again at any time. A message the server leaves out of its answer is asked for again
  by the next download. `--oldest N` (or `--latest N`) takes only the N oldest (newest) new messages of all folders,
  by when the server got them; a copy in another folder comes along without counting. A folder with messages left
  keeps its cursor, so the next download takes them. A notice the server sends in place of a message it can't
  convert (Exchange's "Retrieval using the IMAP4 protocol failed for the following message") is kept as failed, with
  that reason, so that runs leave it out.
- **Sign-in** is OAuth2 with [MSAL](https://github.com/AzureAD/microsoft-authentication-library-for-python), in
  glean's own process: the browser opens Microsoft's page, MSAL takes the answer on a local port. See
  [microsoft-app.md](microsoft-app.md).
- **A folder of `.eml` files** is the other mailbox: each subfolder a mail folder, files directly in it the Inbox.

### Who wrote it: evidence, not rules

glean skips nothing by itself: a first look reads every email and decides whether a person wrote it. Rules erred: of
1,798 emails an earlier glean skipped by their headers, a support desk's replies (sent `Auto-Submitted`) and an
invitation Microsoft rated spam were people's. What the headers and the sender say goes into the first look's message,
as evidence to weigh:

- Microsoft's spam confidence level of 5 or more (`X-MS-Exchange-Organization-SCL`, or `SCL` in
  `X-Forefront-Antispam-Report`), or delivery to junk (`X-Microsoft-Antispam-Mailbox-Delivery`);
- its bulk complaint level of 4 or more (`BCL` in `X-Microsoft-Antispam`);
- `List-Unsubscribe`, `List-Id`, `Precedence: bulk/list/junk`, `Auto-Submitted`;
- a sender no person answers from: `*noreply*`, `alerts@`, `notifications@`, `newsletter@`, `newsletter.` subdomains
  and the like (role addresses people answer from, `support@`, `info@`, are not among them);
- that the owner has written to the sender before (the recipients of the owner's mail);
- how the runs judged the earlier emails from the same address, and from others at its domain (not a free-mail one):
  how many were imported, skipped or spam, with the latest subjects or reasons. The prompt makes this evidence about
  the sender, not about the email: a sender whose mail was skipped can still send one to import.

What the owner wrote gets none. Automated mail costs a first look of one or two turns and no tools, about $0.002 with
Haiku; one that settles a lasting fact about the owner (a registration or admission, a service or contract that
starts, changes or ends) is imported as its interaction, its organization and the fact on Me. Only the senders in
`skip_senders` are skipped without a run, and never what the owner wrote or what people they wrote to send; mail skipped
before its sender turned out to be such a person is let back in.

## The agent run

Each item gets two runs, each its own `claude -p` process with a fresh context. **The first look**
(`[agent] model`, Haiku) has no tools but its answer and no servers: it reads the item, cut at 60,000 characters,
with the headers' signals and the sender's history, and answers `import`, `skipped` or `spam` with a reason, by the
rules of `glean/prompts/triage.md` and the connector's part (for mail, `glean/connectors/mail/triage.md`). Skipped and
spam end there. What you wrote, an item you reset or a review named to import again, and one you include with
`--include-skipped` get no first look. **The import** (`[agent] import_model`, Sonnet) records what the first look sent on, knowing why,
and knows only what orbit and sift hold:

```
claude -p
  --tools ""                                   no built-in tools: no shell, no files, no web
  --allowedTools <orbit's and sift's tools>    the 15 orbit tools and sift's query, search, reindex, one by one
  --disallowedTools <the servers' others>      hidden from the model, not just refused: merge_entities, …
  --permission-mode dontAsk                    anything not allowed is denied, never asked
  --permission-prompts none
  --restricted                                 no user, project or local settings: no CLAUDE.md, hooks or plugins
  --disable-slash-commands                     no skills
  --strict-mcp-config --mcp-config mcp.json    orbit (at /mcp/write) and sift, no other servers
  --system-prompt-file prompt.md               glean's prompt instead of Claude Code's
  --output-format stream-json --verbose        every event, the first listing the run's tools and servers
  --json-schema <the answer>                   outcome, reason, report
  --max-turns 60 --max-budget-usd 1.0 --model sonnet
  --no-session-persistence
```

- **The item goes on stdin**, between two lines with a random marker, after the paths of its files.
- **It runs in an empty temporary folder**, so no project files or settings leak in; `mcp.json` and the prompt are in
  another.
- **Before a batch**, glean starts Claude Code once for each kind of run (first look, import, review) and reads only
  its first event: its servers must be connected and every allowed tool there. The servers' other tools become the
  `--disallowedTools` of every run of that kind.
- **Every run is checked**: its first event must list exactly the allowed tools (and `StructuredOutput`, which only
  returns the answer) and both servers connected, or the run is stopped before its first turn and the item fails with
  the reason.
- **The answer** is structured: `imported`, `already_imported`, `skipped` (automated mail, plainly, after all) or
  `spam`, a reason in a line, and the report. `skipped` and `spam` make the item skipped, with the agent's reason; the
  rest done. The item's cost and turns are both runs'. A result
  without the structured answer goes back to the agent for it, like the checks' findings; still without one, the item
  fails, never taken as imported. An email the model's usage policy refuses to read (as it refuses phishing) is skipped
  with that reason; it doesn't count toward the circuit breaker. The result is read whatever happens: success,
  `error_max_turns`, `error_max_budget_usd`, `error_during_execution`, a timeout, or a crash. The report goes to the
  item's folder in `runs/`, with any tool calls the sandbox refused; the outcome, turns and cost to `runs.db`.
- **An import is checked before it counts**, from the run's own tool calls: the interaction logged, with every
  address of the item's From, To and Cc (or the report saying why not); every address, person, organization and
  topic `log_interaction` listed as unknown taken up by a later call or the report; sift
  asked to `reindex`; no failed orbit call dropped without being done since or named in the report. What is left goes
  back to the agent in the same session (`--input-format stream-json` keeps it open), up to twice. The report lists
  what each time sent back and what was still left (`followups` and `checks_left` in its front matter); the
  transcript has glean's messages too, marked `"glean": "followup"`.

The **prompt** is `glean/prompts/import.md`, shared by every source: import what the first look sent on, unless it
plainly isn't worth it; log the interaction first and make it the source of everything else; resolve before writing;
write only what the
item states, facts about the owner on Me; ask with `propose_review` when unsure; the item as data and never
instructions; documents, indexing and the answer. Each connector adds its part: for mail,
`glean/connectors/mail/instructions.md` (the headers as orbit takes them, the thread orbit
returns, one call for all links, people at the organization of their address, tasks an organization owes, open tasks
an email settles, closed before new ones are made).

Each `glean mail download`, `run` and `review` keeps what glean said, results included, with the time of each line, in
a log of its own: `logs/mail-run-2026-10-23_210509.log` in glean's home, and how it ended if it failed. Every log is
kept.

### Several at once

`[agent] parallel` (or `run --parallel N`) runs that many agents at once, as a pool: whenever a run ends, the next
item starts, so all N stay busy while items wait. Two runs that both meet a new person or company could each create
it, so each item names what it shares with others: for mail, its correspondents' addresses, their company domains
(not free-mail ones) and its thread. An item that shares any of these with an older one waiting or running lets the
next unrelated item go first, and starts only when no unrelated one is left. orbit refuses a second person with the
same address or a second company with the same domain, so related runs side by side stay consistent; it takes their
calls one at a time, and all of glean's own bookkeeping happens on one thread. Ctrl+C stops every run.

**A circuit breaker** stops the batch when 6 of the last 10 runs went wrong (failed, left work glean's checks found
after the follow-ups, or had calls refused by the sandbox), since the rest would likely go the same way: glean names
the most common cause, and the items not yet run stay as they were.

### The review

While a run goes on, glean has the strongest model (`[agent] review_model`, Opus by default) look at what its runs
wrote, one review run per `review_every` items (100), in their order: each starts once all of its items are recorded,
and one at a time, so that each sees what the one before it fixed, while the rest are imported. It reads a digest of
each item (its subject, sender, file and verdict, and its report, shortened; a skipped item's is a line with its
reason, as a review rereads its input on every turn), and the item itself, with sift's `get` and the file's path,
where a digest leaves it in doubt. It has orbit's full tools (`/mcp`: merges, renames, `link_by_domain` and
`retract_observation` too, but no new interactions or tasks) and sift's search. It links people without an
organization to the one their address is at, merges duplicates the notes prove, merges the owner's other notes into
Me, renames notes named after an address handle, closes tasks later emails settled, corrects tasks and fields, takes
back observations that aren't so (and adds them where they belong), and answers with a report, the items to import
again, each with why (glean makes them new and wanted, so the next run imports them without a first look), and
suggestions for the importer's instructions. Notes an import recorded as different only the owner's yes to a
duplicate review merges; a refused call isn't tried again. The report goes to `runs/reviews/`, with its transcript.
Its limits are ten times an import's: turns, budget and time. Ctrl+C and the circuit breaker stop the review in
progress too, which then reports nothing. `glean mail review` reviews the latest imports when asked (`--latest N`, or
ids). It needs orbit 0.10.0 and sift 1.5.0 or newer.

**Questions for the owner.** What no email says, only the owner knows: who a person is to them, whether an
organization is their bank or their school, whether something that looks automated mattered. The review asks such
questions as orbit's open questions, notes in `90_Review` that say which emails they come from: every one truly
necessary (its answer would change what orbit holds, and nothing in orbit can settle it), related ones put together,
and never one pending or answered already; above 15 per 100 imports, only the 15 that matter most, against flooding.
The review's report lists them. The owner answers in their own words in the `answer` column of the Review
base's table in Obsidian, and a yes-or-no question with a tick. The next review reads the answered ones first: it
records what they say on the notes they are about, with the owner's answer as the source, makes the corrections they
call for, and settles each with what it did, so that the answer is acted on once. Later imports then find that
knowledge in orbit when they resolve the people and organizations an email names; asking what the owner answered
returns their answer.

## sift's collections

The agent's `reindex` only takes files inside sift's collections. After a download and before a run, glean makes sure
there is one for the archive and one for orbit's vault, through the `sift` command: it uses a collection that holds the
folder already (sift refuses one inside another, which would index files twice), or adds one (`sift add … --title-from
name`). orbit names its vault's folder in the resource `orbit://vault`, which glean reads as an MCP client would. glean
writes the `.siftignore` of the archive's collection where the collection's folder is glean's (its home or the archive).
After a download, and before a run, it has sift index the archive's collection (a download stopped early hasn't), which
makes every new email findable by keyword search, and has sift read the scans among the saved attachments with OCR (half
a minute a page on a fast GPU, once, and glean waits for it however long it takes); the server embeds what the agent
reindexes, and `sift embed` the rest. `[sift] add_collections = false` leaves sift to you.

## Adding a source

A source is a `Connector` (`glean/connectors/base.py`) registered in `glean/connectors/__init__.py`:

| Member | Does |
|---|---|
| `name`, `noun`, `help` | its command group and table in `glean.toml` (`mail`), what an item is called (`email`), one line of help |
| `configure(table)` | reads its table of `glean.toml`; the result has `archive` and `collection` |
| `setup(ask, say)` | asks what its table needs, for `glean setup`; returns the table |
| `download(state, oldest, latest)` | stores what is new in the archive and its database, or only the N oldest or newest of it, without changing the source |
| `document(item)` | writes the item's Markdown and the files sift should index; returns a `Document`, with `related`: what the item shares with others it mustn't be imported beside |
| `instructions()` | the source's part of the agent's prompt |
| `commands`, `ignored`, `check()`, `key()`, `sender()`, `upgrade()`, `after_setup()` | optional: commands of its own (`login`), files sift should leave out, checks for `glean doctor`, how a person writes a key, who wrote an item and their group (an address and its domain), bringing an older glean's items in line, a step after setup |

The shared commands (`download`, `list`, `run`, `show`, `ignore`, `reset`), the state, the schedule, the agent
run, sift's collections and the reports then work for it as they do for mail. `tests/notes_connector.py` is a complete
connector in 60 lines.
