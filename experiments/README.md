# Experiments

Trying glean with the real agent, with nothing real at stake. Each email imported here is a real Claude Code run.

## A trial on a made-up mailbox

```bash
uv run python experiments/trial.py
uv run python experiments/trial.py --only 01-offer 02-reply   # just these emails
```

It writes a made-up mailbox (`make_mail.py`), creates a new orbit vault and serves it on port 8776, sets up a glean
home of its own, downloads and imports, and prints every run's report. It needs `orbit` on PATH, sift serving on its
usual port, and Claude Code signed in. The folder it prints is kept: open `crm-test/` in Obsidian to see the notes,
and `home/runs/` for each run's report and transcript.

The mailbox is a short thread with a supplier (an offer with a PDF, the owner's reply promising an answer by a date),
an introduction that names a new contact and a job change, a newsletter (which the agent skips), and an email that
tries to give the agent orders. What to look for:

- Hans Müller, Lea Frei and Marco Rossi are created once, at Alpenwerk AG, and matched again in later emails.
- The offer gives a task due at the end of October; the reply moves that task to 28 October instead of adding one.
- The introduction records Lea's move and Marco's new role.
- The odd email is recorded as an email, with a remark in the report; nothing is merged or renamed.
- sift refuses to index the trial's files, which are in none of its collections: the reports say so.

## Your own mail, replayed into a new vault

```bash
uv run python experiments/replay.py --home D:/glean/data --match "Offerte" "Hans Müller" --model sonnet
uv run python experiments/replay.py --home D:/glean/data --sample 20 --seed 1 --status new --parallel 3
uv run python experiments/replay.py --home D:/glean/data --ids c7bd0937b5cf 53cda570860a --model haiku --bare-me
```

It copies emails glean has downloaded already (those with the ids given, those whose subject or sender match, or a
seeded sample) into a home of its own, imports them into a new vault on port 8777 with the real agent, and scores the result: notes per type, the
agent's verdicts, turns and cost, observation and history lines whose source is a file path instead of a link, and
reviews that ask whether two interactions are one. It only reads the home it replays from, and adds no sift
collections. Comparing models or prompts on the same emails takes one run each. With `--bare-me`, the vault's `Me.md`
knows none of the owner's addresses, as after an `orbit init` without `--name` and `--email`; `--orbit` names the
orbit command to run, such as a checkout's.

## A benchmark of your own mail

```bash
uv run python experiments/bench.py select --home D:/glean/data --owner-name "Your Name" --bench D:/glean/data/bench/set-1
uv run python experiments/bench.py annotate --bench D:/glean/data/bench/set-1
uv run python experiments/bench.py run --bench D:/glean/data/bench/set-1 --name haiku-0.7
uv run python experiments/bench.py grade --bench D:/glean/data/bench/set-1 --name haiku-0.7
```

`select` picks about 100 emails, seeded: whole threads, outbound mail, forwards, group mail, attachments, what
glean's old rules or an agent skipped, and imports. `annotate` has Opus and Sonnet each write an answer key, what a
careful assistant would record (the verdict, people and their roles, organizations, tasks, facts about the owner and
others), and Opus settle where they differ; what only you can answer goes to `disputes.md`. `run` replays the emails
oldest first into a new vault with the real agent, and `grade` compares that vault with the key: verdict accuracy,
recall and precision of the people and organizations each interaction links, people across the set (missing,
duplicated, the owner recorded as a person), and, by a Sonnet judge, the tasks and facts. Grades go to
`grades/<name>.md`. Keep `--bench` outside every repository: it holds your mail and what is known about the people in
it.

## Your own mail, against a copy of your vault

```bash
orbit serve D:\crm-test --port 8776      # a copy of the vault
```

Set `[orbit] url = "http://127.0.0.1:8776/mcp/write"` in `glean.toml`, then `glean mail run --oldest 5`, and read
the reports (`glean mail show <id>`) and the notes before pointing glean at the real vault.

## Making the mailbox only

```bash
uv run python experiments/make_mail.py some/folder
```

Then set `[mail] eml_folder` to it.
