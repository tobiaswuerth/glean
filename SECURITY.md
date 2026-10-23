# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
[Report a vulnerability](https://github.com/tobiaswuerth/glean/security/advisories/new). Don't open a public issue.
Expect a first reply within a week.

## Supported versions

Security fixes go into the latest release.

## Scope

**Items are untrusted input.** An email can contain text written to steer an agent ("ignore your instructions
and …"). glean limits what such text could do:

- The importing agent has no shell, file or web tools: only orbit's tools, which validate every write and record its
  source, and sift's search and reindex. orbit's destructive tools (merge, rename, lint fixes, creating notes for
  links) are hidden from it, not just refused.
- The review after a run reads no item, only digests of the runs (which quote items, and are fenced as data). It has
  orbit's merges and renames, to fix duplicates, but no tools to record new interactions or tasks, and the same
  sandbox; the items it names to import again can only be its own batch's. Turn it off with
  `[agent] review_every = 0`; orbit keeps every merged note in `.trash`.
- Each run is started with Claude Code's own sandbox flags, in an empty folder, without the user's settings,
  `CLAUDE.md`, hooks, plugins, skills or other MCP servers. Every run's first event is checked against the allowed
  tools and servers, and a run with anything else is stopped before its first turn.
- The prompt says the item is data to record, never instructions, and the item is fenced by a random marker it can't
  contain. Uncertainty becomes a question for the owner in orbit's review queue.
- Try a new setup on a copy of the vault first, and read the reports (see the README).

**The source is never changed.** IMAP folders are opened with `EXAMINE` and messages fetched with `BODY.PEEK[]`; no
command that changes a mailbox is sent.

**Private data stays local.** The archive, glean's databases and the agents' transcripts hold your mail; keep glean's
home out of synced and shared folders. The OAuth tokens are encrypted with your Windows login (DPAPI); on macOS and
Linux they are in a file only you may read. glean's only network connections are to the mail server, to Microsoft's
sign-in, and those Claude Code makes for the agent; orbit and sift are reached on `127.0.0.1`.
