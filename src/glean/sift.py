"""Keeping sift's collections in line with glean's archives and orbit's vault, so that the agent's `reindex` finds
every file in a collection and nothing is indexed twice or from where it shouldn't be. Through the `sift` command,
which works on the same index as `sift serve`: glean finds the collection that holds a folder, or adds one; keeps
out of it what sift shouldn't read (`.siftignore`); and has sift index what a download brought."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import archive, console, orbit
from .errors import GleanError

IGNORE_FILE = ".siftignore"
IGNORE_HEAD = "# Left out by glean: what it keeps for itself, or what sift finds in another file already"
INDEX_SECONDS = 1800  # a first index of a large archive takes a while


class Sift:
    def __init__(self, command, run=subprocess.run):
        self.command, self.run = list(command), run

    @classmethod
    def find(cls, run=subprocess.run):
        """The sift command on PATH, or None if sift isn't installed."""
        found = shutil.which("sift")
        return cls([found], run) if found else None

    def _call(self, *args, timeout=120):
        try:
            done = self.run(
                [*self.command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GleanError(f"`sift {args[0]}` didn't finish: {e}") from None
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
            raise GleanError(f"`sift {' '.join(args)}` failed: {' '.join(tail) or f'exit code {done.returncode}'}")
        return done

    def collections(self):
        """sift's collections, each a dict with its `name` and `path`."""
        try:
            return json.loads(self._call("status", "--json").stdout)["collections"]
        except (ValueError, KeyError, TypeError):
            raise GleanError("`sift status --json` gave no collections: sift 1.2 or newer is needed") from None

    def add(self, folder, name):
        self._call("add", str(folder), "--name", name, "--title-from", "name")

    def index(self, name):
        """Bring the collection's index up to date with its folder: new, changed and gone files (keyword search; the
        server embeds for semantic search what the agent reindexes, `sift embed` the rest)."""
        return self._call("index", name, timeout=INDEX_SECONDS).stderr.strip().splitlines()[-1:]


def holder(collections, folder):
    """The collection whose folder is `folder` or holds it, the nearest if several do; None if none does."""
    folder = _norm(folder)
    held = [c for c in collections if folder.is_relative_to(_norm(c["path"]))]
    return max(held, key=lambda c: len(_norm(c["path"]).parts), default=None)


def _norm(path):
    return Path(os.path.normcase(Path(path).resolve()))  # as the file system compares: on Windows without case


def collection(sift, folder, name, ignored=(), owned=(), kept_out=()):
    """The name of the sift collection that holds `folder`; one called `name` (or `name-2`, … if that is taken) is
    added if none does. `ignored` patterns, and the `kept_out` folders inside the collection's folder, are written to
    its .siftignore if its folder is `folder` or one of `owned` (glean's home, say): glean doesn't write into folders
    it doesn't keep. Returns (name, added)."""
    collections = sift.collections()
    found = holder(collections, folder)
    added = found is None
    if added:
        taken = {c["name"] for c in collections}
        name = next(n for n in (name, *(f"{name}-{i}" for i in range(2, 100))) if n not in taken)
        root = Path(folder)
        root.mkdir(parents=True, exist_ok=True)
        ignore(root, [*ignored, *_inside(root, kept_out)])  # before sift first reads the folder
        sift.add(root, name)
        return name, added
    root = Path(found["path"])
    left_out = [*ignored, *_inside(root, kept_out)]
    if left_out and any(_norm(root) == _norm(f) for f in (folder, *owned)):
        ignore(root, left_out)
    return found["name"], added


def _inside(root, folders):
    """The patterns of those of `folders` inside `root`, from its top: `/runs/`."""
    root = _norm(root)
    return [f"/{(_norm(f).relative_to(root)).as_posix()}/" for f in folders if _norm(f).is_relative_to(root)]


def ignore(folder, patterns):
    """Make the .siftignore in `folder` leave out `patterns` (adding those it lacks, keeping everything else in it).
    Returns the patterns added."""
    path = Path(folder) / IGNORE_FILE
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        text = ""
    present = {line.strip() for line in text.splitlines()}
    missing = [p for p in patterns if p not in present]
    if missing:
        head = "" if IGNORE_HEAD in present else ("\n" if text.strip() else "") + IGNORE_HEAD + "\n"
        body = text if not text or text.endswith("\n") else text + "\n"
        archive.write_atomic(path, (body + head + "".join(f"{p}\n" for p in missing)).encode("utf-8"))
    return missing


def keep(cfg, connector, say=console.say, index=False):
    """Make sure sift has collections for the connector's archive and orbit's vault, and keeps out of them what it
    shouldn't read; with `index`, bring the archive's collection up to date with what was downloaded. Returns the
    names of the two collections, {"archive": …, "vault": …}. Problems are said, not raised: sift's collections
    don't stop a download or a run."""
    names = {"archive": connector.collection, "vault": cfg.sift.crm_collection}
    if not cfg.sift.add_collections:
        return names
    found = Sift.find()
    if found is None:
        say("warning: sift isn't on PATH, so glean can't check its collections; or set [sift] add_collections = false")
        return names
    try:
        owned, runs = (cfg.home, connector.archive), (cfg.home / archive.RUNS,)
        names["archive"], added = collection(
            found, connector.archive, connector.collection, connector.ignored, owned, runs
        )
        if added:
            say(f"sift: added the collection {names['archive']!r} for {connector.archive}")
        vault = cfg.orbit_vault or orbit.vault(cfg.orbit_url)
        if vault is not None:
            names["vault"], added = collection(found, vault, cfg.sift.crm_collection)
            if added:
                say(f"sift: added the collection {names['vault']!r} for orbit's vault, {vault}")
        if index:
            for line in found.index(names["archive"]):
                say(f"sift: {line}")
    except GleanError as e:
        say(f"warning: {e}")
    return names


def check(cfg, connector):
    """Which collections hold the archive and orbit's vault, for `glean doctor`: a list of (ok, line)."""
    found = Sift.find()
    if found is None:
        return [(False, "sift: the sift command isn't on PATH")]
    try:
        collections = found.collections()
    except GleanError as e:
        return [(False, f"sift: {e}")]
    lines = []
    vault = cfg.orbit_vault or orbit.vault(cfg.orbit_url)
    for what, folder in ((f"the {connector.name} archive", connector.archive), ("orbit's vault", vault)):
        if folder is None:
            lines.append((False, "sift: orbit didn't say where its vault is; set [orbit] vault in glean.toml"))
            continue
        held = holder(collections, folder)
        if held:
            lines.append((True, f"sift: the collection {held['name']!r} holds {what}, {folder}"))
        else:
            next_step = "glean adds one with the next download or run" if cfg.sift.add_collections else "add one"
            lines.append((cfg.sift.add_collections, f"sift: no collection holds {what}, {folder}: {next_step}"))
    return lines
