"""The state of a home, in two kinds of database. Beside each source's archive, its own: mails.db beside mails/, with
every item downloaded, where it was found, where the download left off, and what the download, the skip rules and the
owner decided about each item. In the home, runs.db: what the agent made of each item, and every run; the runs' files
are in runs/ (see `archive.run_folder`). Deleting runs.db and runs/ starts the imports afresh, with every item as it
was downloaded, and nothing downloaded again.

Items are keyed by what identifies them across folders (an email's Message-ID), not by a server's numbers, which
change. The sources themselves are never marked: these databases are the only record of what was processed."""

import hashlib
import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path

from . import archive
from .errors import GleanError

SCHEMA_VERSION = 2  # of mails.db and runs.db, in their user_version; 2: outcomes.wanted
ID_LENGTH = 12  # hex digits of an item's short id; longer on a clash
STATUSES = ("new", "done", "failed", "skipped", "ignored")  # an item's, as the two databases make it
FOUND = ("new", "skipped", "failed", "ignored")  # what the download, the skip rules or the owner decided
OUTCOMES = ("done", "failed", "skipped")  # what the runs made of an item
OLD_STATE = "state.db"  # glean 0.3 and older kept everything in it

ARCHIVE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {{s}}.items (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    date TEXT NOT NULL,
    utc TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    container TEXT NOT NULL,
    direction TEXT,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({", ".join(f"'{s}'" for s in FOUND)})),
    reason TEXT,
    document TEXT,
    downloaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS {{s}}.items_by_time ON items (utc);
CREATE TABLE IF NOT EXISTS {{s}}.locations (
    container TEXT NOT NULL,
    ref TEXT NOT NULL,
    item TEXT NOT NULL REFERENCES items (id),
    PRIMARY KEY (container, ref)
);
CREATE INDEX IF NOT EXISTS {{s}}.locations_by_item ON locations (item);
CREATE TABLE IF NOT EXISTS {{s}}.cursors (container TEXT PRIMARY KEY, cursor TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS {{s}}.contacts (address TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS {{s}}.marks (name TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA {{s}}.user_version = {SCHEMA_VERSION};
"""
RUNS_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS outcomes (
    source TEXT NOT NULL,
    item TEXT NOT NULL,
    status TEXT CHECK (status IS NULL OR status IN ({", ".join(f"'{s}'" for s in OUTCOMES)})),
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    report TEXT,
    processed_at TEXT,
    wanted TEXT,
    PRIMARY KEY (source, item)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    item TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    turns INTEGER,
    cost_usd REAL,
    duration_ms INTEGER,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS runs_by_item ON runs (item);
PRAGMA main.user_version = {SCHEMA_VERSION};
"""
# An item as both databases make it, one SELECT per source: what its runs made of it, unless the owner ignores it or
# no run made anything of it, as when it never ran, was reset, or its runs were stopped (whose reason it takes).
ITEM = """SELECT '{name}' AS source, m.id, m.key, m.date, m.utc, m.title, m.author, m.container, m.direction, m.path,
    m.sha256, m.size,
    CASE WHEN o.status IS NULL OR m.status = 'ignored' THEN m.status ELSE o.status END AS status,
    CASE WHEN m.status = 'ignored' OR coalesce(o.status, o.reason) IS NULL THEN m.reason ELSE o.reason END AS reason,
    coalesce(o.attempts, 0) AS attempts, coalesce(o.cost_usd, 0) AS cost_usd, m.document, o.report, m.downloaded_at,
    o.processed_at, o.wanted
    FROM "{name}".items m LEFT JOIN main.outcomes o ON o.source = '{name}' AND o.item = m.id"""
ITEM_COLUMNS = [
    "source", "id", "key", "date", "utc", "title", "author", "container", "direction", "path", "sha256", "size",
    "status", "reason", "attempts", "cost_usd", "document", "report", "downloaded_at", "processed_at", "wanted",
]  # fmt: skip
NO_ITEMS = "SELECT " + ", ".join(f"NULL AS {c}" for c in ITEM_COLUMNS) + " WHERE 0"  # a home without sources


def database(folder):
    """The database of the archive in `folder`: beside it, with its name, mails.db for mails/."""
    folder = Path(folder)
    return folder.parent / f"{folder.name}.db"


@dataclass(frozen=True)
class Item:
    """One item of a source, such as an email. A connector fills in what it found; the state the rest."""

    source: str  # the connector's name, e.g. "mail"
    key: str  # what identifies the item within its source, e.g. an email's normalized Message-ID
    date: str  # ISO 8601, with its offset
    title: str  # e.g. an email's subject
    author: str  # e.g. an email's From
    container: str  # where it was found first, e.g. a mail folder
    direction: str | None  # inbound or outbound, where that applies
    path: str  # the original, relative to the source's archive
    sha256: str  # of the original
    size: int  # of the original, in bytes
    status: str = "new"
    reason: str | None = None  # why it has its status: a skip rule, the error of a failed run
    id: str = ""
    attempts: int = 0
    cost_usd: float = 0.0
    document: str | None = None  # the normalized Markdown, relative to the archive
    report: str | None = None  # the last agent run's report, relative to the home's runs/
    downloaded_at: str = ""
    processed_at: str | None = None
    wanted: str | None = None  # why the owner or a review asked to import it, which a first look then doesn't judge
    containers: tuple[str, ...] = ()  # every place it was found


COLUMNS = [f.name for f in fields(Item) if f.name != "containers"]
DOWNLOADED = [
    "id", "key", "date", "title", "author", "container", "direction", "path", "sha256", "size", "status", "reason",
    "document", "downloaded_at",
]  # fmt: skip


class State:
    """A home's state: runs.db in `home`, and the database beside each archive of `archives`, {source: folder}."""

    def __init__(self, home, archives, now=lambda: datetime.now().astimezone()):
        home = Path(home)
        if (home / OLD_STATE).exists():
            raise GleanError(
                f"{home / OLD_STATE} is the state of glean 0.3 or older, which this glean doesn't read: it keeps "
                f"downloads and runs apart, in their own databases. To start a new home, move {home} away and run "
                "`glean setup`."
            )
        for name in archives:
            if not name.isidentifier() or name.lower() in ("main", "temp"):
                raise ValueError(f"{name!r} can't be a source name: letters, digits and underscores")
        home.mkdir(parents=True, exist_ok=True)
        self.now, self.sources = now, tuple(archives)
        path = home / f"{archive.RUNS}.db"
        self.db = sqlite3.connect(path)
        self.db.create_function("casefold", 1, str.casefold, deterministic=True)  # lower() knows only ASCII
        self.db.row_factory = sqlite3.Row
        try:
            self._check(path, "main")
            self._upgrade()
            self.db.executescript(RUNS_SCHEMA)
            for name, folder in archives.items():
                path = database(folder)
                path.parent.mkdir(parents=True, exist_ok=True)
                self.db.execute(f'ATTACH DATABASE ? AS "{name}"', (str(path),))
                self._check(path, f'"{name}"')
                self.db.executescript(ARCHIVE_SCHEMA.format(s=f'"{name}"'))
            union = " UNION ALL ".join(ITEM.format(name=name) for name in archives) or NO_ITEMS
            self.db.executescript(f"CREATE TEMP VIEW items AS {union}; PRAGMA foreign_keys = ON;")
        except sqlite3.DatabaseError as e:
            self.db.close()
            raise GleanError(f"{path} isn't a glean database ({e}): move it away, and glean starts a new one") from None
        except GleanError:
            self.db.close()
            raise

    def _check(self, path, schema):
        version = self.db.execute(f"PRAGMA {schema}.user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise GleanError(f"{path} was written by a newer glean (schema {version}); update glean")

    def _upgrade(self):
        """Bring a runs.db of an older glean in line: glean 0.12's outcomes lack `wanted`. In one write transaction, so
        that a second glean opening it at the same moment waits, then finds it done."""
        self.db.execute("BEGIN IMMEDIATE")
        columns = [r[1] for r in self.db.execute("PRAGMA main.table_info(outcomes)")]
        if columns and "wanted" not in columns:
            self.db.execute("ALTER TABLE outcomes ADD COLUMN wanted TEXT")
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.db.close()

    def _stamp(self):
        return self.now().isoformat()

    def _of(self, source):
        """The schema of a source's archive database, to write to."""
        if source not in self.sources:
            raise ValueError(f"glean has no archive for the source {source!r}")
        return f'"{source}"'

    def _item(self, row):
        places = self.db.execute(
            f"SELECT DISTINCT container FROM {self._of(row['source'])}.locations WHERE item = ? ORDER BY container",
            (row["id"],),
        ).fetchall()
        return Item(**{c: row[c] for c in COLUMNS}, containers=tuple(p[0] for p in places))

    def _one(self, where, args):
        row = self.db.execute(f"SELECT * FROM items WHERE {where}", args).fetchone()
        return row and self._item(row)

    def _again(self, item):
        return self.find(item.source, item.key)

    # Downloading

    def cursor(self, source, container):
        """Where the last download of a container ended, as the connector put it; None before the first."""
        row = self.db.execute(f"SELECT cursor FROM {self._of(source)}.cursors WHERE container = ?", (container,))
        found = row.fetchone()
        return found and found[0]

    def set_cursor(self, source, container, cursor):
        with self.db:
            self.db.execute(
                f"INSERT INTO {self._of(source)}.cursors VALUES (?, ?, ?) ON CONFLICT (container) "
                "DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
                (container, cursor, self._stamp()),
            )

    def add_contacts(self, source, addresses):
        """Record addresses the owner has written to: what they send is personal, whatever its headers say."""
        with self.db:
            self.db.executemany(
                f"INSERT OR IGNORE INTO {self._of(source)}.contacts VALUES (?)",
                [(a.strip().lower(),) for a in addresses],
            )

    def contacts(self, source):
        return {r[0] for r in self.db.execute(f"SELECT address FROM {self._of(source)}.contacts")}

    def mark(self, source, name):
        """A value glean keeps for a source, such as the version whose rules its items follow; None if unset."""
        row = self.db.execute(f"SELECT value FROM {self._of(source)}.marks WHERE name = ?", (name,)).fetchone()
        return row and row[0]

    def set_mark(self, source, name, value):
        with self.db:
            self.db.execute(f"INSERT OR REPLACE INTO {self._of(source)}.marks VALUES (?, ?)", (name, value))

    def last_download(self, source):
        return self.db.execute(f"SELECT max(updated_at) FROM {self._of(source)}.cursors").fetchone()[0]

    def find(self, source, key):
        return self._one("source = ? AND key = ?", (source, key))

    def known_refs(self, source, container):
        rows = self.db.execute(f"SELECT ref FROM {self._of(source)}.locations WHERE container = ?", (container,))
        return {r[0] for r in rows}

    def new_id(self, source, key):
        """The id a new item gets: the start of a digest of its source and key, longer if that is taken, in any
        source."""
        digest = hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()
        length = ID_LENGTH
        while self.db.execute("SELECT 1 FROM items WHERE id = ?", (digest[:length],)).fetchone():
            length += 1
        return digest[:length]

    def add(self, item, container, ref):
        """Store a new item found at `ref` in `container`, and return it with its id (the one it has, if any)."""
        schema = self._of(item.source)
        utc = datetime.fromisoformat(item.date).astimezone(UTC).isoformat()
        stored = replace(item, id=item.id or self.new_id(item.source, item.key), downloaded_at=self._stamp())
        values = {c: getattr(stored, c) for c in DOWNLOADED} | {"utc": utc}
        with self.db:
            self.db.execute(
                f"INSERT INTO {schema}.items ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                list(values.values()),
            )
            self.db.execute(f"INSERT INTO {schema}.locations VALUES (?, ?, ?)", (container, ref, stored.id))
        return self._again(stored)

    def locate(self, item, container, ref):
        """Record another place where a known item is found."""
        with self.db:
            self.db.execute(
                f"INSERT OR IGNORE INTO {self._of(item.source)}.locations VALUES (?, ?, ?)", (container, ref, item.id)
            )

    def set_document(self, item, document):
        """Record where the item's document is, relative to the archive."""
        with self.db:
            self.db.execute(f"UPDATE {self._of(item.source)}.items SET document = ? WHERE id = ?", (document, item.id))
        return self._again(item)

    def set_direction(self, item, direction):
        """Record whether the owner wrote an item, as their addresses tell now."""
        with self.db:
            self.db.execute(
                f"UPDATE {self._of(item.source)}.items SET direction = ? WHERE id = ?", (direction, item.id)
            )
        return self._again(item)

    def set_status(self, item, status, reason=None):
        """Set what the download, the skip rules or the owner decided: new, skipped, failed or ignored. What a run made
        of an item stays, and is the item's status, unless it is ignored."""
        if status not in FOUND:
            raise ValueError(
                f"status {status!r} isn't the download's or the owner's to set; those are {', '.join(FOUND)}"
            )
        with self.db:
            self.db.execute(
                f"UPDATE {self._of(item.source)}.items SET status = ?, reason = ? WHERE id = ?",
                (status, reason, item.id),
            )
        return self._again(item)

    def reset(self, item, wanted=None):
        """Make an item new again, to be imported again or after all: what its runs made of it is forgotten, their
        number and records kept, so that its next run knows it is a retry. `wanted`: why the owner or a review asks
        for it to be imported, which it is then without being judged again, until a run imports or skips it."""
        with self.db:
            self.db.execute(
                f"UPDATE {self._of(item.source)}.items SET status = 'new', reason = NULL WHERE id = ?", (item.id,)
            )
            self.db.execute(
                "INSERT INTO outcomes (source, item, wanted) VALUES (?, ?, ?) ON CONFLICT (source, item) DO UPDATE SET "
                "status = NULL, reason = NULL, wanted = excluded.wanted",
                (item.source, item.id, wanted),
            )
        return self._again(item)

    # Running

    def pending(self, source, limit, statuses=("new",), newest=False):
        """The `limit` oldest items with one of these statuses (all, for None), or with `newest` the `limit` newest;
        oldest first either way, the order to process them in."""
        marks = ", ".join("?" * len(statuses))
        rows = self.db.execute(
            f"SELECT * FROM items WHERE source = ? AND status IN ({marks}) "
            f"ORDER BY utc {'DESC' if newest else 'ASC'} LIMIT ?",
            (source, *statuses, -1 if limit is None else limit),
        ).fetchall()
        return [self._item(r) for r in (reversed(rows) if newest else rows)]

    def items(self, source, status=None, limit=None, newest=True):
        """A source's items, newest first, or oldest first."""
        where, args = ("source = ?", [source]) if status is None else ("source = ? AND status = ?", [source, status])
        rows = self.db.execute(
            f"SELECT * FROM items WHERE {where} ORDER BY utc {'DESC' if newest else 'ASC'} LIMIT ?",
            (*args, -1 if limit is None else limit),
        )
        return [self._item(r) for r in rows]

    def get(self, source, ref, normalize=None):
        """An item by its id, the start of its id, or its key."""
        rows = self.db.execute(
            "SELECT * FROM items WHERE source = ? AND (substr(id, 1, ?) = ? OR key = ? OR key = ?)",
            (source, len(ref), ref.lower(), ref, normalize(ref) if normalize else ref),
        ).fetchall()
        exact = [r for r in rows if r["id"] == ref.lower()]
        if len(rows) > 1 and not exact:
            raise GleanError(f"{ref} matches several {source} items: give more of the id")
        if not rows:
            raise GleanError(f"no {source} item with the id or key {ref}: `glean {source} list` shows them")
        return self._item((exact or rows)[0])

    def set_outcome(self, item, status, reason=None):
        """Record what became of an item that couldn't run, such as one whose document couldn't be written."""
        if status not in OUTCOMES:
            raise ValueError(f"an outcome is {', '.join(OUTCOMES)}, not {status!r}")
        with self.db:
            self.db.execute(
                "INSERT INTO outcomes (source, item, status, reason) VALUES (?, ?, ?, ?) ON CONFLICT (source, item) "
                "DO UPDATE SET status = excluded.status, reason = excluded.reason",
                (item.source, item.id, status, reason),
            )
        return self._again(item)

    def start_run(self, item):
        with self.db:
            return self.db.execute(
                "INSERT INTO runs (source, item, started_at) VALUES (?, ?, ?)", (item.source, item.id, self._stamp())
            ).lastrowid

    def finish_run(
        self, run, item, *, status, outcome, cost_usd, turns, duration_ms, document=None, report=None, reason=None
    ):
        """Record how a run ended, and what it made of its item: `status`, or, for None (a run that was stopped), what
        the item had before."""
        if status is not None and status not in OUTCOMES:
            raise ValueError(f"an outcome is {', '.join(OUTCOMES)}, not {status!r}")
        stamp = self._stamp()
        with self.db:
            self.db.execute(
                "UPDATE runs SET finished_at = ?, outcome = ?, turns = ?, cost_usd = ?, duration_ms = ?, reason = ? "
                "WHERE id = ?",
                (stamp, outcome, turns, cost_usd, duration_ms, reason, run),
            )
            self.db.execute(
                "INSERT INTO outcomes (source, item, status, reason, attempts, cost_usd, report, processed_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?) ON CONFLICT (source, item) DO UPDATE SET "
                "status = coalesce(excluded.status, status), reason = excluded.reason, attempts = attempts + 1, "
                "cost_usd = cost_usd + excluded.cost_usd, report = coalesce(excluded.report, report), "
                "processed_at = excluded.processed_at, "
                "wanted = CASE WHEN excluded.status IN ('done', 'skipped') THEN NULL ELSE wanted END",
                (item.source, item.id, status, reason, cost_usd or 0, report, stamp),
            )
            if document:
                self.db.execute(
                    f"UPDATE {self._of(item.source)}.items SET document = ? WHERE id = ?", (document, item.id)
                )
        return self._again(item)

    def judged(self, item, key):
        """(author, title, status, reason) of the other items of the item's source that runs imported or skipped and
        whose author has `key` in it, in any case; the latest first."""
        return self.db.execute(
            f"SELECT m.author, m.title, o.status, o.reason FROM {self._of(item.source)}.items m "
            "JOIN main.outcomes o ON o.source = ? AND o.item = m.id "
            "WHERE m.id != ? AND o.status IN ('done', 'skipped') AND instr(casefold(m.author), ?) > 0 "
            "ORDER BY o.processed_at DESC",
            (item.source, item.id, key.casefold()),
        ).fetchall()

    def runs(self, item):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM runs WHERE source = ? AND item = ? ORDER BY id", (item.source, item.id)
            )
        ]

    # Reporting

    def counts(self):
        """Items per status, per source."""
        counts = {}
        for source, status, n in self.db.execute(
            "SELECT source, status, count(*) FROM items GROUP BY source, status ORDER BY source"
        ):
            counts.setdefault(source, {})[status] = n
        return {s: {k: c[k] for k in STATUSES if k in c} for s, c in counts.items()}

    def total_cost(self, source):
        return self.db.execute("SELECT coalesce(sum(cost_usd), 0) FROM items WHERE source = ?", (source,)).fetchone()[0]
