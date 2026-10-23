"""Replay emails glean downloaded already into a new vault, with the real agent, and score the result: for trying
prompts and models on real mail with nothing real at stake. Reads the given glean home; writes only into --dir.

    uv run python experiments/replay.py --home D:/glean/data --owner you@example.com --model sonnet \
        --match "Offerte" "Hans Müller"
    uv run python experiments/replay.py --home D:/glean/data --owner you@example.com --sample 20 --seed 1 \
        --status new done

The emails are copied into a folder of .eml files (one subfolder per mail folder), glean downloads them from there
into a home of its own, and imports them into a new orbit vault served on --port, with a sift of its own on
--sift-port that indexes the replay's mail and vault. The folder is kept; its vault can be opened in Obsidian."""

import argparse
import json
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from trial import serving, settings

from glean.state import State


def emails(home):
    """(id, title, author, container, path, status) of every email a glean home holds, oldest first, with the status
    its runs gave it; its databases opened read-only."""
    db = sqlite3.connect(f"{(home / 'mails.db').as_uri()}?mode=ro", uri=True)
    status, join = "m.status", ""
    if (home / "runs.db").exists():
        db.execute("ATTACH DATABASE ? AS runs", (f"{(home / 'runs.db').as_uri()}?mode=ro",))
        status = "CASE WHEN o.status IS NULL OR m.status = 'ignored' THEN m.status ELSE o.status END"
        join = "LEFT JOIN runs.outcomes o ON o.source = 'mail' AND o.item = m.id"
    sql = f"SELECT m.id, m.title, m.author, m.container, m.path, {status} FROM items m {join} ORDER BY m.utc"
    return db.execute(sql).fetchall()


def chosen(home, matches, sample, seed, statuses, ids=()):
    """The items to replay: those with these ids, or whose title or sender has one of `matches`, else a seeded
    sample."""
    rows = emails(home)
    if ids:
        return [r for r in rows if r[0] in ids]
    if matches:
        return [r for r in rows if any(m.lower() in (r[1] + " " + r[2]).lower() for m in matches)]
    pool = [r for r in rows if not statuses or r[5] in statuses]
    return random.Random(seed).sample(pool, min(sample, len(pool)))


def score(vault, reports):
    """What a reader of the vault would trip over."""
    folders = [p.relative_to(vault).parts[0] for p in vault.rglob("*.md")]
    notes = Counter(f for f in folders if f[:2].isdigit() and f not in ("95_Bases", "99_System"))
    plain_sources = [  # observation and history lines whose source is a file path, not a link to an interaction
        f"{path.name}: {line[:100]}"
        for path in vault.rglob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("- ") and " · " in line and re.search(r"[A-Za-z]:\\|/glean/", line.split(" · ")[-2])
    ]
    reviews = [p.name for p in (vault / "90_Review").glob("*.md")]
    interaction_reviews = [r for r in reviews if r.count(" Email \u2013 ") >= 2]
    verdicts = Counter(r["verdict"] for r in reports)
    return {
        "notes": dict(notes),
        "verdicts": dict(verdicts),
        "turns": sum(r["turns"] for r in reports),
        "cost_usd": round(sum(r["cost_usd"] for r in reports), 3),
        "plain_sources": plain_sources,
        "reviews": reviews,
        "reviews_of_interactions": interaction_reviews,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--home", type=Path, required=True, help="the glean home whose emails are replayed (read only)")
    p.add_argument("--ids", nargs="*", default=[], help="the emails with these ids")
    p.add_argument("--match", nargs="*", default=[], help="emails whose subject or sender has one of these")
    p.add_argument("--sample", type=int, default=10, help="else this many emails, picked at random")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--status", nargs="*", default=[], help="pick only from items with these statuses")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--port", type=int, default=8777)
    p.add_argument("--sift-port", type=int, default=8779, help="the replay's own sift's port")
    p.add_argument("--dir", type=Path, help="where to keep the replay (default: a new temporary folder)")
    p.add_argument("--owner", required=True, help="the account's address")
    p.add_argument("--owner-name", default="Owner", help="the name of the vault's owner, Me")
    p.add_argument("--my-addresses", nargs="*", default=[], help="the owner's other addresses, Me's and glean's")
    p.add_argument("--bare-me", action="store_true", help="a vault whose Me.md has no name or address")
    p.add_argument("--orbit", help="the orbit command to run (default: the one on PATH)")
    args = p.parse_args()
    root = (args.dir or Path(tempfile.mkdtemp(prefix="glean-replay-"))).resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} isn't empty: give a new --dir")
    items = chosen(args.home.resolve(), args.match, args.sample, args.seed, args.status, set(args.ids))
    eml, vault, home = root / "eml", root / "vault", root / "home"
    for item_id, _, _, container, path, _ in items:
        folder = eml if container == "Inbox" else eml / container.replace("/", "-")
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.home / "mails" / path, folder / f"{item_id}.eml")
    print(f"replaying {len(items)} emails in {root}")
    orbit = args.orbit or shutil.which("orbit") or sys.exit("orbit isn't on PATH")
    owner = [] if args.bare_me else ["--name", args.owner_name, "--email", args.owner]
    owner += [arg for address in args.my_addresses if not args.bare_me for arg in ("--email", address)]
    subprocess.run([orbit, "init", str(vault), *owner], check=True, capture_output=True)
    home.mkdir()
    (home / "glean.toml").write_text(
        f'[agent]\nmodel = "{args.model}"\nparallel = {args.parallel}\n\n'
        + settings(args.port, args.sift_port)
        + f'[mail]\naddress = "{args.owner}"\nmy_addresses = {json.dumps(args.my_addresses)}\n'
        f'eml_folder = "{eml.as_posix()}"\n',
        encoding="utf-8",
    )
    with serving(root, vault, orbit, args.port, args.sift_port) as env:
        glean = [sys.executable, "-m", "glean"]
        subprocess.run([*glean, "mail", "download"], env=env, check=True)
        subprocess.run([*glean, "mail", "run", "--oldest", str(len(items)), "--no-review"], env=env)  # bench.py reviews
    reports = []
    for report in sorted((home / "runs").rglob("report.md")):
        front = report.read_text(encoding="utf-8").split("---\n")[1]
        reports.append({k: json.loads(v) for k, _, v in (line.partition(": ") for line in front.splitlines())})
    result = score(vault, reports)
    with State(home, {"mail": home / "mails"}) as state:
        replayed = state.items("mail")
    result["statuses"] = dict(Counter(item.status for item in replayed))
    result["reasons"] = [f"{item.title[:40]!r}: {item.reason}" for item in replayed if item.reason]
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nThe replay is in {root}: the vault in vault/, glean's home in home/.")


if __name__ == "__main__":
    main()
