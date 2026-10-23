# Contributing

Thanks for helping. Bug reports and feature ideas are welcome as [issues](https://github.com/tobiaswuerth/glean/issues);
for security problems, see [SECURITY.md](SECURITY.md).

## Setup

```bash
git clone https://github.com/tobiaswuerth/glean
cd glean
uv sync
uv run pre-commit install
uv run pytest --cov
```

Read [docs/how-it-works.md](docs/how-it-works.md) first: it describes the pipeline, the archive, the state and
the agent's sandbox, which every change keeps to.

## Code layout

| Module | What it does |
|---|---|
| `cli.py` | The `glean` command: `setup`, `status`, `doctor`, and the commands of each source |
| `pipeline.py` | Choosing items, scheduling them (related ones one after the other), and importing them: document, agent run, record; the agent's prompt and message |
| `agent.py` | One sandboxed `claude -p` run: the command line, the checks of its first event, reading its structured answer, sending it back with what `checks.py` finds |
| `checks.py` | What an import must have done before it counts, read from the run's tool calls |
| `review.py` | The review of a batch's imports by a stronger model, from digests of their runs (`prompts/review.md`) |
| `state.py` | The state: each archive's database (items, where they were found, cursors, contacts, marks) and `runs.db` (outcomes, runs) |
| `sift.py` | Keeping sift's collections in line with the archive and orbit's vault, through the `sift` command |
| `orbit.py` | Where orbit's vault is: asked of orbit as an MCP client connects |
| `archive.py` | Item folders, safe and unique file names, atomic writes, front matter |
| `config.py` | `glean.toml`: the shared settings, and a `Table` reader the connectors use for theirs |
| `wizard.py` | `glean setup`, also on first use |
| `settings.py` | `GLEAN_HOME`, the one environment variable, read once |
| `console.py`, `errors.py` | What glean tells the user, on stderr; `GleanError`, whose message says what to do next |
| `prompts/import.md` | The agent's system prompt, shared by every source |
| `connectors/base.py` | `Connector`, what every source provides, and `Document` |
| `connectors/mail/` | The mail source: `options.py` (its settings), `mailbox.py` (the interface, and `.eml` folders), `imap.py`, `oauth.py`, `message.py` (parsing), `html.py`, `skip.py` (what headers and senders say, as evidence; the owner's skip_senders), `markdown.py` (the document), `instructions.md` (the agent's part) |

Results go to stdout from `cli.py` alone; everything else glean says goes to stderr. No module reads the environment
except `settings.py`.

## How changes are made

- **Tests first.** Write a test that fails for the expected reason, then the least code that makes it pass. Every bug
  fix comes with a regression test; never weaken or skip a test to make a change pass.
- **Full coverage.** `uv run pytest --cov` requires 100% line and branch coverage. Tests use a temporary
  `GLEAN_HOME`, synthetic mail (`tests/mail/samples.py`), a fake IMAP server (`tests/mail/fake_imap.py`) and a fake
  Claude Code (`tests/fake_claude.py`): no network, no account, no agent. Before a pull request, run them on the oldest
  supported Python too: `uv run --python 3.11 --isolated --no-dev --group test pytest -q`.
- **Prompts are tried, not only tested.** A change to the prompts or to the sandbox is tried with the real agent on
  the made-up mailbox (`uv run python experiments/trial.py`) and on real mail in a new vault
  (`uv run python experiments/replay.py`), and the reports and notes read, before it is merged.
- **Tests never reach real services.** A fixture keeps every test away from the machine's sift and orbit; tests that
  need them use a fake `sift` command or a local HTTP server.
- **Shared, or the connector's.** What depends on the kind of source belongs in its connector; what doesn't belongs in
  the shared modules, so that every source gets it. `tests/notes_connector.py` keeps the shared parts honest: it is a
  whole connector in a few lines.
- **No private data in the repository.** Tests and experiments use invented people and `.example` domains. `data/`,
  the usual `GLEAN_HOME` of a clone, is ignored by git.
- **Quality gates.** pre-commit runs on every commit and again in CI: ruff (lint and format), whitespace and line
  endings, YAML/TOML checks, `validate-pyproject`, workflow and Dependabot schema checks, actionlint and codespell.
  Files are UTF-8 with Unix line ends (`.gitattributes`); some Windows editors and scripts write CRLF or a byte order
  mark, so check the diff after editing with one.
- **Pull requests.** `main` is protected: changes arrive through pull requests, which are squash-merged once every CI
  check passes. CI runs the tests on Linux, macOS and Windows for Python 3.11 to 3.15, and builds and checks the
  package.
- **Changelog.** Note user-visible changes under "Unreleased" in [CHANGELOG.md](CHANGELOG.md), in the
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Versions follow
  [Semantic Versioning](https://semver.org/).

## Releasing

1. In a pull request, set `version` in `pyproject.toml`, and move the "Unreleased" changes in `CHANGELOG.md` under a
   section for that version, `## [0.2.0] - 2026-11-30`, with its link at the bottom. Run `uv lock` so `uv.lock`
   carries the new version.
2. Merge it. That's all: once CI has passed on the merge commit, the release workflow sees a version with a changelog
   section and no tag, and publishes that commit.
   - It builds the package and publishes it to [PyPI](https://pypi.org/project/glean-importer/) as `glean-importer`,
     through trusted publishing (no tokens). The `pypi` environment deploys from `main` only.
   - It tags the commit CI tested `v0.2.0`, and creates a GitHub release with that version's changelog. Release tags
     can't be moved or deleted.
   - It doesn't run the tests itself: it starts only after CI passed on that exact commit, and never for a failed
     run. A version without a changelog section isn't released, so `main` can carry unreleased work.
