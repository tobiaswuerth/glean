"""The `glean` command: `setup`, `status` and `doctor`, and one group of commands per source, such as `glean mail`,
with the shared ones (download, list, run, show, ignore, reset) and the source's own (login, logout). Results go to
stdout; everything else glean says goes to stderr."""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

from . import __version__, agent, archive, config, console, logs, pipeline, review, settings, sift, wizard
from .connectors import CONNECTORS
from .errors import GleanError
from .state import STATUSES, State

ARROWS = {"inbound": "<-", "outbound": "->"}


def parser():
    p = argparse.ArgumentParser(
        prog="glean", description="Imports what your sources hold into orbit (a personal CRM) and sift (search)."
    )
    p.add_argument("--version", action="version", version=f"glean {__version__}")
    commands = p.add_subparsers(dest="command", metavar="command")
    setup = commands.add_parser("setup", help="set glean up, or set it up again", description="Set glean up.")
    setup.set_defaults(run=lambda args: setup_command(args))
    status = commands.add_parser("status", help="what glean holds, per source", description="What glean holds.")
    status.add_argument("--json", action="store_true", help="as JSON")
    status.set_defaults(run=lambda args: status_command(args))
    doctor = commands.add_parser(
        "doctor", help="check Claude Code, orbit, sift and each source", description="Check glean's setup."
    )
    doctor.set_defaults(run=lambda args: doctor_command(args))
    for name, connector in CONNECTORS.items():
        group = commands.add_parser(name, help=connector.help, description=f"{connector.help[0].upper()}"
                                    f"{connector.help[1:]}.")  # fmt: skip
        sub = group.add_subparsers(dest="action", metavar="action", required=True)
        _shared(sub, name, connector)
        for action, command in connector.commands.items():
            c = sub.add_parser(action, help=command.help, description=command.help[0].upper() + command.help[1:] + ".")
            command.arguments(c)
            c.set_defaults(run=lambda args, command=command: with_connector(args, command.run, state=False))
    return p


def _shared(sub, name, connector):
    nouns = f"{connector.noun}s"
    download = sub.add_parser("download", help=f"download new {nouns}; the source is never changed")
    some = download.add_mutually_exclusive_group()
    some.add_argument("--oldest", type=_positive, metavar="N", help=f"only the N oldest new {nouns}; the next download "
                      "takes those left")  # fmt: skip
    some.add_argument("--latest", type=_positive, metavar="N", help=f"only the N newest new {nouns}")
    download.set_defaults(run=lambda args: with_connector(args, download_command))

    def choosing(c, default):
        which = c.add_mutually_exclusive_group()
        which.add_argument(
            "--oldest",
            type=_positive,
            metavar="N",
            help=f"the N oldest {nouns}, so that orbit grows in the order they came"
            + (f" (default: {default})" if default else ""),
        )
        which.add_argument("--latest", type=_positive, metavar="N", help=f"instead, the N newest {nouns}")
        c.set_defaults(default_count=default)
        c.add_argument("--retry-failed", action="store_true", help="include those that failed before")
        c.add_argument(
            "--include-skipped", action="store_true", help="include those skipped before, by a skip rule or the agent"
        )

    listing = sub.add_parser("list", help=f"the {nouns} waiting to be imported, oldest first, as run takes them")
    choosing(listing, 20)
    listing.add_argument("--status", choices=STATUSES, help=f"instead, the {nouns} with this status")
    listing.add_argument("--json", action="store_true", help="as JSON")
    listing.set_defaults(run=lambda args: with_connector(args, list_command))
    run = sub.add_parser("run", help=f"import {nouns} into orbit and sift, oldest first, one agent run each")
    choosing(run, None)
    run.add_argument("ids", nargs="*", metavar="ID", help=f"import these {nouns} (an id, its start, or its key)")
    run.add_argument("--dry-run", action="store_true", help="write the documents and show what the agent would get")
    run.add_argument(
        "--parallel", type=_positive, metavar="N", help="agent runs at once (default: glean.toml's [agent] parallel)"
    )
    run.add_argument("--no-review", action="store_true", help="no review of the imports afterwards")
    run.set_defaults(run=lambda args: with_connector(args, run_command))
    checking = sub.add_parser("review", help="review the last imports: duplicates, verdicts, misplaced facts")
    checking.add_argument("ids", nargs="*", metavar="ID", help=f"review the imports of these {nouns}")
    checking.add_argument(
        "--latest", type=_positive, metavar="N", help="the N newest imported (default: [agent] review_every, or 100)"
    )
    checking.set_defaults(run=lambda args: with_connector(args, review_command))
    show = sub.add_parser("show", help=f"one {connector.noun}: its state, files and last report")
    show.add_argument("id", help="its id, the start of it, or its key")
    show.set_defaults(run=lambda args: with_connector(args, show_command))
    for action, status, help in (
        ("ignore", "ignored", f"never import these {nouns}"),
        ("reset", "new", f"import these {nouns} again, or after all"),
    ):
        c = sub.add_parser(action, help=help)
        c.add_argument("ids", nargs="+", metavar="ID")
        c.set_defaults(run=lambda args, status=status: with_connector(args, lambda *given: mark(*given, status)))


def _positive(text):
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("a whole number above 0")
    return int(text)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # a console or file that can't show a character gets "?", not a crash
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return e.code
    if not args.command:
        p.print_help()
        return 0
    try:
        return args.run(args)
    except (GleanError, OSError) as e:  # OSError: the file system's, such as a folder glean may not write to
        console.say(f"glean: {e}")
        return 1
    except KeyboardInterrupt:
        console.say("glean: stopped")
        return 130


def load():
    """The configuration; on first use, in a terminal, the setup wizard first."""
    home = settings.current().home
    path = home / "glean.toml"
    if not path.exists() and sys.stdin.isatty():
        console.say("glean isn't set up yet: starting the setup.")
        wizard.run(home)
    return config.load(path)


def with_connector(args, command, state=True):
    """Run a source's command with its connector, and the state database unless `state` is False."""
    args.config = load()
    connector = CONNECTORS[args.command](args.config.source(args.command), args.config.home)
    if not state:
        return command(connector, args)
    with open_state(args.config) as db:
        return command(connector, args, db)


def open_state(cfg):
    """The home's state: runs.db, and the database of every source's archive, as the ids of all are unique."""
    return State(cfg.home, {name: options.archive for name, options in cfg.sources.items()})


def setup_command(args):
    wizard.run(settings.current().home)
    return 0


def logged(command):
    """A source's command that keeps its own log (see logs)."""

    def run(connector, args, state):
        name = f"{connector.name}-{command.__name__.removesuffix('_command')}"
        with logs.kept(args.config.home, name, datetime.now()):
            return command(connector, args, state)

    return run


@logged
def download_command(connector, args, state):
    with pipeline.lock(connector.home):
        connector.upgrade(state)
        found = connector.download(state, args.oldest, args.latest)
        sift.keep(args.config, connector, index=True)
    places = ", ".join(f"{n:,} in {p}" for p, n in found.places.items())
    console.show(
        f"{console.count(found.new, f'new {connector.noun}')}" + (f" ({places})" if places else "")
        + f", {found.skipped:,} of them skipped by your skip_senders; {found.known:,} known already"
        + (f"; {found.waiting:,} more to download" if found.waiting else "")
    )  # fmt: skip
    return 0


def _row(item):
    when = archive.local_time(datetime.fromisoformat(item.date))
    author = item.author if len(item.author) <= 30 else item.author[:29] + "…"
    title = item.title if len(item.title) <= 60 else item.title[:59] + "…"
    arrow = ARROWS.get(item.direction, "  ")
    return f"{item.id}  {when:%Y-%m-%d %H:%M}  {item.status:<7}  {arrow} {author:<30}  {title}"


def _as_dict(item):
    return dict(item.__dict__) | {"containers": list(item.containers)}


def list_command(connector, args, state):
    try:
        with pipeline.lock(connector.home):
            connector.upgrade(state)
    except pipeline.Busy:  # the glean at work brought them in line already
        pass
    count, newest = _which(args)
    if args.status:
        items = state.items(connector.name, args.status, count, newest)
    else:
        items = pipeline.choose(state, connector, count, newest, args.retry_failed, args.include_skipped)
    if args.json:
        print(json.dumps([_as_dict(i) for i in items], indent=2))  # ASCII: valid wherever it is written to
        return 0
    for item in items:
        print(_row(item))
    if not items:
        console.say(f"no {connector.noun}s to show; `glean {connector.name} download` fetches new ones")
    return 0


def _which(args):
    """How many items `--oldest` or `--latest` asks for (else the command's default), and whether the newest."""
    if args.latest is not None:
        return args.latest, True
    return (args.oldest if args.oldest is not None else args.default_count), False


@logged
def run_command(connector, args, state):
    count, newest = _which(args)
    if not args.ids and count is None:
        raise GleanError(f"say which {connector.noun}s: --oldest N (as they came), --latest N, or their ids")
    with pipeline.lock(connector.home):
        connector.upgrade(state)
        items = pipeline.choose(state, connector, count, newest, args.retry_failed, args.include_skipped, args.ids)
        if not items:
            console.say(f"no {connector.noun}s waiting; `glean {connector.name} download` fetches new ones")
            return 0
        runner = None if args.dry_run else agent.Agent(args.config)
        names = None if args.dry_run else sift.keep(args.config, connector)
        counts = pipeline.run(
            args.config, connector, state, runner, items, out=console.show,
            dry_run=args.dry_run, names=names, parallel=args.parallel or args.config.agent.parallel,
        )  # fmt: skip
        if counts:
            console.say(", ".join(f"{n} {s}" for s, n in counts.items()))
        if not args.dry_run and args.config.agent.review_every and not args.no_review:
            review.run(args.config, connector, state, agent.Agent(args.config, role=agent.REVIEW), items)
    return 1 if counts.get("failed") else 0


@logged
def review_command(connector, args, state):
    count = args.latest or args.config.agent.review_every or 100
    with pipeline.lock(connector.home):
        if args.ids:
            items = [state.get(connector.name, i, connector.key) for i in args.ids]
        else:
            items = [i for i in state.items(connector.name) if i.status in ("done", "skipped") and i.report][-count:]
        if not items:
            console.say("nothing imported yet to review")
            return 0
        runner = agent.Agent(args.config, role=agent.REVIEW)
        review.run(args.config, connector, state, runner, items, every=max(count, len(items)))
    return 0


def show_command(connector, args, state):
    item = state.get(connector.name, args.id, connector.key)
    archive_root = connector.archive
    lines = [
        f"id:         {item.id}",
        f"key:        {item.key}",
        f"status:     {item.status}" + (f" ({item.reason})" if item.reason else ""),
        f"date:       {item.date}",
        f"title:      {item.title}",
        f"author:     {item.author}",
        f"direction:  {item.direction or ''}",
        f"found in:   {', '.join(item.containers)}",
        f"original:   {archive_root / item.path}",
        f"document:   {archive_root / item.document if item.document else '(not written yet)'}",
        f"attempts:   {item.attempts}, ${item.cost_usd:.2f}",
    ]
    lines += [
        f"  run {run['started_at'][:19]}: {run['outcome']}, {run['turns']} turns, ${run['cost_usd'] or 0:.2f}"
        for run in state.runs(item)
    ]
    print("\n".join(lines))
    report = item.report and args.config.home / archive.RUNS / item.report
    if report and report.is_file():
        print(f"\nreport ({report}):\n")
        print(report.read_text(encoding="utf-8").split("---\n", 2)[-1].strip())
    return 0


def mark(connector, args, state, status):
    for ref in args.ids:
        found = state.get(connector.name, ref, connector.key)
        item = state.reset(found) if status == "new" else state.set_status(found, status)
        print(f"{item.status:<7} {_row(item)}")
    return 0


def status_command(args):
    cfg = load()
    report = {"home": str(cfg.home), "config": str(cfg.path), "sources": {}}
    with open_state(cfg) as state:
        counts = state.counts()
        for name, options in cfg.sources.items():
            report["sources"][name] = {
                "archive": str(options.archive),
                "collection": options.collection,
                "items": counts.get(name, {}),
                "last_download": state.last_download(name),
                "cost_usd": round(state.total_cost(name), 4),
            }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"home:    {report['home']}")
    print(f"config:  {report['config']}")
    for name, s in report["sources"].items():
        noun = CONNECTORS[name].noun
        total = sum(s["items"].values())
        parts = ", ".join(f"{n:,} {status}" for status, n in s["items"].items())
        print(f"\n{name}:")
        print(f"  archive:        {s['archive']} (sift collection {s['collection']!r})")
        print(
            f"  {noun}s:" + " " * max(1, 14 - len(noun)) + console.count(total, noun) + (f": {parts}" if parts else "")
        )
        print(f"  last download:  {s['last_download'] or 'never'}")
        print(f"  agent cost:     ${s['cost_usd']:.2f}")
    return 0


def doctor_command(args):
    cfg = load()
    checks = [(True, f"config: {cfg.path}")]
    try:
        claude = agent.find_claude(cfg.agent.claude)
        found = subprocess.run([str(claude), "--version"], capture_output=True, text=True, timeout=60).stdout
        version = agent.version(found)
        new_enough = version >= agent.MIN_VERSION
        need = ".".join(map(str, agent.MIN_VERSION))
        checks.append((new_enough, f"claude: {claude}, {found.strip()}" + ("" if new_enough else f"; needs {need}+")))
        auth = subprocess.run([str(claude), "auth", "status"], capture_output=True, text=True, timeout=60)
        checks.append((auth.returncode == 0, "claude: signed in" if auth.returncode == 0 else "claude: not signed in"))
    except (GleanError, OSError, subprocess.TimeoutExpired) as e:
        checks.append((False, f"claude: {e}"))
    for name, url in (("orbit", cfg.orbit_url), ("sift", cfg.sift.url)):
        checks.append(_reachable(name, url))
    if all(ok for ok, _ in checks):
        try:
            tools = agent.Agent(cfg).prepare()
            checks.append((True, f"agent: {len(tools)} tools, orbit and sift connected, nothing else"))
        except GleanError as e:
            checks.append((False, f"agent: {e}"))
        if cfg.agent.review_every:
            try:
                tools = agent.Agent(cfg, role=agent.REVIEW).prepare()
                checks.append((True, f"review: {len(tools)} tools on orbit's /mcp, merges included"))
            except GleanError as e:
                checks.append((False, f"review: {e}"))
    if cfg.orbit_url.rstrip("/").endswith("/mcp"):
        checks.append(
            (True, "orbit: /mcp serves merges and renames too, which glean hides; /mcp/write leaves them out")
        )
    for name, options in cfg.sources.items():
        connector = CONNECTORS[name](options, cfg.home)
        checks += connector.check() + sift.check(cfg, connector)
    for ok, line in checks:
        print(f"{'ok' if ok else '!!'}  {line}")
    return 0 if all(ok for ok, _ in checks) else 1


def _reachable(name, url):
    try:
        urllib.request.urlopen(url, timeout=5).close()
    except urllib.error.HTTPError as e:  # it answers; MCP endpoints refuse a plain GET
        e.close()
    except (urllib.error.URLError, OSError) as e:
        return False, f"{name}: nothing answers at {url} ({getattr(e, 'reason', e)}): start `{name} serve`"
    return True, f"{name}: answers at {url}"
