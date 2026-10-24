# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The import records health matters (a treatment, an illness, a sick day) as personal facts like any other; its prompt
  had it leave them out.
- The owner's addresses, for an import, are glean.toml's and every other one orbit resolves to Me: runs took the
  owner's other addresses for strangers'.
- A shared mailbox (a team's hr@, a support address) stays off the note of the person who signs from it.
- The vacancies one sender proposes unasked in a day make one task to answer them, not one per email.
- A review sends a skipped email back to be imported only for a fact about the owner that Me doesn't hold yet: one
  run's reviews sent three bills of one insurer back, each to show the same.

### Fixed

- Mail downloaded before from an address the owner adds to `my_addresses` becomes theirs, once: outbound, its
  recipients contacts, set aside by no skip rule, and its document says so; mail from an address taken out is someone
  else's again. It stayed as it was downloaded.

## [0.13.0] - 2026-10-24

A first look decides, a stronger model imports, and the reviews run alongside and read what they doubt. Needs orbit
0.10.0 and sift 1.5.0 for the review.

### Added

- A first look at each item: `[agent] model` (Haiku) with no tools and no servers, only its answer, judges by the text
  whether the item is worth recording (`import`, `skipped` or `spam`, with a reason), with the headers' signals and
  the sender's history as evidence; its prompt is `prompts/triage.md` and the connector's `triage.md`. What it judges
  worth it goes to the import, by `[agent] import_model` (new, Sonnet by default), which knows why and may still call
  it skipped or spam where that is plain. The item's cost, turns and report count both runs.
- What the owner wrote is imported without a first look: one skipped the owner's one-word note to a friend, with a
  file, as nothing lasting. The import still skips the owner's tests to themselves.
- An item reset by the owner (`glean mail reset`) or named by a review to import again is imported without a first
  look, with why: a review's `reimport` now names why for each, and runs.db keeps it (`outcomes.wanted`, schema 2;
  glean 0.12's runs.db gains it when first opened, and an older glean then refuses it).
- The review reads an email where its digest leaves doubt: each digest names the email's file, which the review reads
  with sift's `get` before it changes, merges, asks or sends anything back.
- The review takes back an observation that isn't so, or sits on the wrong note, with orbit's `retract_observation`,
  and records it where it belongs.

### Changed

- Reviews run while the rest of a batch is imported: one of every `review_every` items, in order, once all of them
  are recorded, and one at a time. Ctrl+C and the circuit breaker stop the review in progress too, which then reports
  nothing.
- `glean doctor` checks the first look, the import and the review each.
- `run --dry-run` heads each message with its item, and shows a first look's message, or an import's for an item
  that gets none.
- What a model said about an item (a first look's or an earlier run's reason, a review's why) reaches the import as
  one line of at most 200 characters, as the item's text could steer it.

### Fixed

- Ctrl+C or the circuit breaker lets no run start after it: a run whose process hadn't started yet ran on unkilled,
  and the batch waited for it.
- The circuit breaker records every run that ended with the one that tripped it: they were taken as stopped, and
  their items imported again by the next run.

## [0.12.1] - 2026-10-24

### Changed

- The import prompt imports an automated email that settles a case or request of the owner's (an account restored, a
  request accepted or refused, a case closed, a refund made), so that the task waiting for it is closed, and a
  receipt of the owner's request only when it quotes the owner's own words.
- An email the model's usage policy refuses to read (as it refuses phishing) is skipped with that reason instead of
  failed, and doesn't count toward the circuit breaker: retrying gets the same refusal, and a stretch of phishing could
  stop a batch. `glean mail reset` brings one back.

## [0.12.0] - 2026-10-24

Open tasks an email settles are closed first; the review finds the names of people known by an address handle.

### Changed

- The import prompt closes the open tasks an email settles before it makes new ones, with the signs the reviews kept
  finding: the answer came, a reply or thank-you shows the owner did it, a later step shows an earlier one took place
  (an offer after the interview), the case was closed, it was declined or no longer applies. On a new benchmark of
  complete threads, settled tasks found 0.42 → 0.50, task precision 0.84 → 0.89, runs sent back 15 → 6 (two runs each).
- The review looks up the name of a person named after an address handle: it searches sift for the address (another
  email, a CC list or a newsletter often names the person) before it renames the note or leaves it.

## [0.11.2] - 2026-10-24

### Changed

- The import prompt imports the receipt of an application (a job, a course, a place), whose fact goes on Me, and a
  company's receipt of the owner's request when it quotes the request, the first message of that thread.
- The review leaves an imported application receipt, as it leaves other automated mail that settles a fact about the
  owner.

### Fixed

- A run whose result came without its structured answer is asked for it again, and without one counts as failed, to
  try again: it was taken as imported, also where the agent had said in words that it skipped the email.

## [0.11.1] - 2026-10-24

### Fixed

- A download or run waits for sift's index of the archive however long it takes: a first index of a large archive,
  scans read with OCR, was stopped after half an hour, unfinished, and its worker processes kept glean waiting. glean
  says beforehand that it indexes.

## [0.11.0] - 2026-10-24

Each run hears how the sender's earlier mail was judged; a company's answer to the owner's question is imported.

### Added

- Each run hears how the runs judged the earlier emails from the same sender, and from others at its domain (not a
  free-mail one): how many were imported, skipped or spam, with the latest subjects or reasons. The prompt makes it
  evidence about the sender, not about the email, so that look-alike mail gets the same verdict while a sender whose
  mail was skipped can still send one to import.

### Changed

- The import prompt names a question about an order or a bill among the owner's requests whose answer is imported: a
  customer-service reply read as an order notice about half of the time.

## [0.10.0] - 2026-10-24

Download only some, facts about the owner from automated mail, an index that is current for every run.

### Added

- `glean mail download --oldest N` (or `--latest N`) downloads only the N oldest (newest) new emails of all folders;
  the next download takes those left, and the result line says how many are. A download says how many folders it
  looks through before it scans them, which takes a while in a large mailbox.

### Changed

- The import prompt imports automated mail that settles a lasting fact about the owner (a registration or admission, a
  service or contract that starts, changes or ends) as the interaction, its organization and the fact on Me, and a
  company's answer to the owner's own request; facts about the owner go on Me only; a person's mail is imported however
  short or passing. A copy of an email updates its task; every open task the email settles is closed. The owner's own
  addresses are logged too.
- The review renames a note spelled as its address spells the name once an email shows how the person writes it,
  searches Me and sift before asking the owner, and leaves imported automated mail that settles a fact about the owner.

### Fixed

- A run has sift index the archive's collection before it starts: a download stopped before its end left what it had
  stored unsearchable, so the agents found no earlier mail of a thread or a person.
- A notice Exchange or Outlook.com sends over IMAP in place of a message it can't convert ("Retrieval using the IMAP4
  protocol failed for the following message") is kept as failed with that reason, which runs leave out, not as a new
  email.

## [0.9.0] - 2026-10-23

People at their organization, tasks that later mail settles, answers that aren't asked again, a log per run.

### Added

- Each `glean mail download`, `run` and `review` writes what it says, results included, with the time of each line,
  to a log of its own in `logs/` of glean's home, down to how it ended if it failed. Every log is kept.
- The review links people without an organization to the one their address is at (orbit's `link_by_domain`),
  renames notes named after an address handle, and closes tasks that a later email settled.

### Changed

- The import prompt, rewritten: the same rules, grouped and shorter. New in it: every sender and recipient who is a
  person is recorded, a bare address too (named by what the address spells, else its handle); an organization's
  domain includes its subdomains, and orbit gives a person the organization of their address; an organization may
  owe a task (a refund, a fix); each request or promise is a task of its own, recorded as of the email's date however
  long ago; open tasks an email settles are closed; facts come from the email itself, never from later ones sift
  holds; the owner's affiliations (school, employer, services they hold) go on Me; an email the owner wrote to a
  person is imported however short; comment notices of shared documents are skipped.
- An unsure match is provisional with a review, never also recorded as a different person; a refused call isn't
  repeated unchanged; what the owner answered is never asked again.
- A review reads a skipped item's digest as one line, with its reason.
- Needs orbit 0.8.0 or newer, whose tools the review uses.

## [0.8.1] - 2026-10-23

### Changed

- Prompts, tool descriptions, tests and docs use made-up examples only. Releases 0.2.0 to 0.8.0 are no longer on
  PyPI.
- Needs orbit 0.7.1 or newer, whose tools the review uses.

## [0.8.0] - 2026-10-23

Questions only the owner can answer, and answers in their own words.

### Added

- The review asks the owner what no email says (who a person is to them, whether an organization is their bank):
  every question truly necessary, related ones put together, under a ceiling of 15 per 100 imports against
  flooding, as orbit's open questions in `90_Review`; its report lists them. The owner answers in words, typed into
  the `answer` column of orbit's Review table in Obsidian. The next review reads the answers first, records what they
  say with the owner as the source, makes the corrections they call for, and settles each in orbit, once.

### Changed

- Needs orbit 0.7.0 (open questions answered in the Review base's table, `settle_review`); `glean doctor` says so for
  an older one.

## [0.7.0] - 2026-10-23

Measured on a benchmark of 100 real emails, against 0.6.0 with the same model (Haiku): participants recall .86 →
.98 at the same precision (.89 → .90), tasks recall .35 → .59, owner facts .50 → .58, people missed 5 → 2, verdicts
.97 → .99, at about the same cost ($0.53 → $0.56 per 100 emails).

### Added

- A review after each run: one run of a stronger model (`[agent] review_model`, Opus by default) for every
  `review_every` imports (100), reading a digest of each import, not the emails. It merges duplicates orbit's notes
  prove, merges the owner's other notes into Me, corrects misplaced tasks and fields, makes items it finds wrongly
  judged new to import again, and suggests changes to the importer's instructions; its report goes to
  `runs/reviews/`. `glean mail review` reviews the latest imports when asked; `run --no-review` leaves it out. On the
  benchmark's 100 emails it cost $1.15, merged a duplicate, closed an answered task, removed three guessed
  relationships and named the two test mails imported by mistake.

- A circuit breaker: a batch stops when 6 of its last 10 runs went wrong (failed, left work undone after the
  follow-ups, or had calls refused), and names the most common cause; the items not yet run stay as they were.
- glean's checks know the email's addresses: one of its From, To or Cc that the interaction doesn't name, nor the
  report, sends the run back. An agent had logged a class's email with only the owner in To, so orbit listed none of
  the twelve others as unknown.
- `experiments/bench.py`: a benchmark of real emails. It picks a seeded mix (whole threads, outbound, forwards, group
  mail, attachments, what the old rules or an agent skipped), has Opus and Sonnet write an answer key independently
  and Opus settle where they differ, replays the emails into a new vault, and grades it: verdicts, links, people
  across the set (missing, duplicated, the owner as a person), tasks and facts by a judge. `bench.py review` lets the
  review fix a copy of a run, to grade it again.

### Changed

- The import prompt is a third denser, and says what the benchmark found missing: every From, To and Cc address in
  `log_interaction`, however long the list (a class or a family on one email are people the owner knows); the
  owner's other accounts linked as Me with an address review, never as a person; names resolved with their
  organization; people named without an address recorded when the email says who they are; no field set on a guess
  (a relationship, an employer); nothing secret (passwords, card and ID numbers, health details); independent calls
  in one turn; tasks for questions put to the owner and for what the owner waits on; the answer to an application
  of the owner's, even a platform's template, recorded; tests the owner sent skipped.

## [0.6.0] - 2026-10-23

Quality checked while the run is still open, and no rules that decide in the agent's place.

### Added

- An import is checked before it counts, from the run's own tool calls: the interaction logged, every address,
  person, organization and topic orbit listed as unknown taken up or explained, sift asked to index, no failed orbit
  call dropped without a word. What is left goes back to the agent in the same session, up to twice; the report lists
  what each time sent back and what was still left (`followups`, `checks_left`), the transcript glean's messages.
- The run's message says what the email's headers and sender say: Microsoft's spam and bulk ratings, mailing-list and
  `Auto-Submitted` headers, a no-reply sender, and whether the owner wrote to the sender before, as evidence for the
  agent to weigh.

### Changed

- glean skips nothing by itself: of 1,798 emails its header rules skipped in a real mailbox, a support desk's replies
  and an invitation Microsoft rated spam were people's. The agent judges every email now; automated mail costs a run
  without a tool call, about $0.002 with Haiku. Mail the old rules skipped, and no agent ran on, is new again after
  the upgrade, and every email gets its document, spam too. Replayed on 56 emails the rules had skipped: 52 skipped
  by the agent, and 4 imported that people wrote (two support replies, a recruiter's invitation Microsoft sent to
  junk, a CTO's message through a no-reply address), for $0.14.
- `skip_senders` is yours alone: glean adds no senders of its own.

### Removed

- `skip_spam` and `skip_bulk`: glean names them when they are still in `glean.toml`, to be removed.

## [0.5.1] - 2026-10-23

What 600 real emails imported oldest first showed.

### Fixed

- An email without a subject: the agent wrote `"subject": ,` for the front matter's `null`, which Claude Code refused
  as JSON, 8 times, 7 of them in one run. It leaves out what the front matter has as null now.
- Mailboxes of an office (`zurich@shop.example`) became people named after them; they are the organization's now,
  and a person's name is one the email gives, an address's handle only for a personal address no one is named for.
- The agent asked whether an email sent twice, or again with changes, was the same interaction as the first, "so
  that one can be deleted", which no answer can do; it no longer asks.

Replayed with Haiku on the 12 emails behind these: no call refused for its JSON, the mailboxes linked as their
organizations, and no review.

## [0.5.0] - 2026-10-23

orbit grows as the mailbox did: oldest first, batch after batch.

### Added

- `run --oldest N` and `list --oldest N`: the N oldest emails not yet imported. Imported in that order, the people
  and tasks an earlier email creates exist when a later one mentions, answers or settles them. With `--latest N`,
  which takes the newest, the next batch went further back in time: a reply came before the email it answers, and a
  task it had settled was opened afterwards, and stayed open.

### Changed

- `list` without `--oldest` or `--latest` shows the 20 oldest emails waiting, as `run --oldest 20` takes them, no
  longer the 20 newest; `list --status` lists oldest first unless `--latest` is given. The setup's next steps, the
  README and the error of a `run` without a count suggest `--oldest`. `run` still needs a count or ids, so that no
  mailbox of thousands starts by accident.

## [0.4.1] - 2026-10-23

### Changed

- Haiku is the default model, no longer Sonnet. In runs of 100 real emails each, Haiku cost $0.27 and Sonnet $4.08;
  Sonnet 5.5 also called tools without their `mcp__orbit__` prefix, or by names that don't exist, 17 times in 10
  runs, each a wasted turn. A `glean.toml` written before keeps the model it names: set `[agent] model = "haiku"`.

### Fixed

- The `glean.toml` that setup writes said related items always go one after the other; since 0.3.2 they do so only
  while other items can run.

## [0.4.0] - 2026-10-23

Downloads and runs are kept apart, so that the imports can start afresh without downloading anything again.

### Changed

- A home holds `mails/` and `mails.db` (what the downloads brought: originals, documents, attachments, every email,
  where it was found, where the downloads left off, what the skip rules and you decided) and `runs/` and `runs.db`
  (what the agent's runs made of each email: outcomes, attempts, cost, reports, transcripts). Deleting `runs.db` and
  `runs/` makes every email as it was downloaded again. Each source's archive has its database beside it, named
  after it.
- The mail archive and its sift collection are `mails` by default, no longer `mail`.
- Reports and transcripts are in `runs/mails/<the email's folder>/`, no longer in a `.glean/` folder beside the email.
  Where a sift collection holds the whole home, glean's `.siftignore` leaves `runs/` out.
- `reset` makes an email new whatever its runs made of it, and keeps the number of its runs, so that its next run
  knows it is a retry; `ignore` takes precedence over what the runs made of an email.

### Removed

- A home of glean 0.3 or older, with everything in one `state.db`, isn't read: glean says so. Start a new home and
  download again.

## [0.3.2] - 2026-10-23

What a run of 100 real emails with `--parallel 8` showed.

### Changed

- `--parallel N` keeps N agents busy as long as emails wait. Related emails, which share people, a company or a
  thread, still go one after the other while unrelated ones can run meanwhile, but no longer leave agents idle: in
  that run, all 8 ran until the 73rd email, then fewer and fewer, down to one, as the last emails were nearly all from
  one school. Replayed with that run's durations, the schedule now takes a third of the time (2.1 minutes, not 6.6).
- The agent is told the owner's addresses, `[mail] address` and `my_addresses`: wherever one appears, that is Me in
  orbit, and one orbit doesn't know yet is added to Me. In a vault made without `orbit init --email`, agents had
  linked the owner by address or by name, which orbit refused.

### Fixed

- Five automated emails were imported "as asked": the user message began "Import this email", which the agent took
  for the owner's wish. It now asks for the decision first, and only its line about an email included on purpose
  overrides the rules for automated mail. Replayed with Haiku on those five and on three of the owner's own, in a
  vault whose `Me.md` had no address: the five skipped, the three imported, the owner's address added to Me and
  Me linked, with no call refused.
- The user message named the email's original `.eml`, of no use to an agent without file tools; one agent passed it
  to sift's `reindex`, which refuses files its `.siftignore` leaves out. The message gives the original's sha256
  alone.
- The README asks for sift 1.3.1, as 1.3.0's server skipped every attachment the agent gave its `reindex`.

## [0.3.1] - 2026-10-23

### Changed

- The agent logs an email with the organizations it concerns, as orbit 0.3 takes them: those orbit knows are linked
  at once, the others come back as unknown, to be resolved and created with the email as their source, organizations
  before the people who work there. Replayed on 15 real emails against the prompt of 0.3.0: the same organizations
  and links, a third fewer `link_entities` calls, at the same cost.

## [0.3.0] - 2026-10-23

For orbit 0.3 and sift 1.3.

### Changed

- glean finds orbit's vault in its resource `orbit://vault`, no longer in the words of its instructions.
- Needs orbit 0.3 and sift 1.3 or newer. With sift 1.3, the `sift index` after each download reads scanned
  attachments with OCR, once each.

## [0.2.0] - 2026-10-22

What the first imports of real mail showed: spam and mass mail that got through, runs on automated mail, sift's
collections to set up by hand, and one run at a time.

### Added

- Spam and mass mail are skipped by Outlook.com's own ratings (SCL, BCL, the junk folder), without an agent run; what
  people the owner wrote to send is never skipped, and mail skipped before its sender turned out to be one is let in
  again. Senders that are machines (`*noreply*`, `alerts@`, …) are skipped too.
- Each run ends with a structured answer: imported, already imported, skipped (automated mail) or spam, with a reason.
  Mail the agent judges automated or spam is skipped, with that reason, instead of done.
- Runs side by side: `[agent] parallel` and `run --parallel N`. Emails that share people, a company or a thread still
  go one after the other, oldest first.
- glean keeps sift's collections in line: it adds one for its archive and one for orbit's vault (which it asks orbit
  for) when none holds them, keeps the original `.eml` files out with a `.siftignore`, and has sift index each
  download. `glean doctor` shows which collections hold what.
- Every downloaded email but spam gets its Markdown document at once, so sift can search all mail.
- `experiments/replay.py` replays downloaded mail into a new vault and scores the result.

### Changed

- The prompts follow orbit 0.2: the email's addresses in `log_interaction`, all links in one call, the thread from
  its answer, tasks that continue the thread's. The agent decides first whether an email is worth recording, logs the
  interaction before anything else and makes it the source of every write, keeps facts about the owner on Me, and no
  longer asks whether two interactions are one.
- orbit's `/mcp/write` endpoint, without merges, renames and lint, is the default `[orbit] url`.
- Lists in `glean.toml` (`exclude_folders`, `skip_senders`) add to glean's own instead of replacing them, so new
  defaults reach existing setups; the setup writes only what you must say.
- Mail downloaded by an older glean follows this version's skip rules and gets its documents, once, before the next
  download, run or list. The state database gains its new tables by itself.
- Needs orbit 0.2, sift 1.2 and Claude Code 2.1.290 or newer.

### Fixed

- Headers written as raw 8-bit text, such as an unencoded `Müller` in `To:`, are read as UTF-8 (or Windows-1252)
  instead of breaking the email's document.

## [0.1.0] - 2026-10-22

The first release.

### Added

- Importing items into orbit and sift, one sandboxed Claude Code agent run per item, oldest first: `glean <source>
  download`, `list`, `run` (with `--retry-failed`, `--include-skipped` and `--dry-run`), `show`, `ignore` and
  `reset`, and `glean setup`, `status` and `doctor`.
- The mail source: IMAP with OAuth2 for Outlook.com and Hotmail (read-only, every folder but junk, trash and drafts),
  or a folder of `.eml` files; emails as Markdown with their headers as front matter, attachments sift can read saved
  beside them; newsletters and notifications skipped without an agent run.
- The agent's sandbox, checked on every run: orbit's reading and writing tools and sift's search and reindex, nothing
  else; the user's settings, `CLAUDE.md`, hooks, plugins and other servers left out.
- A setup wizard on first use, and `experiments/` to try imports on a made-up mailbox and a new vault.
- Python 3.11 to 3.15, on Windows, macOS and Linux.
