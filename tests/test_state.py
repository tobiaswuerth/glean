"""The state of a home: every item glean has downloaded, beside its archive (mails.db beside mails/), and what the
agent made of each, in runs.db; deleting runs.db starts the imports afresh."""

import sqlite3

import pytest

from glean.errors import GleanError
from glean.state import Item, State, database

from .conftest import NOW


def item(key="a@example.com", date="2026-10-14T09:32:00+02:00", **kwargs):
    fields = {
        "source": "mail",
        "key": key,
        "date": date,
        "title": f"About {key}",
        "author": "Hans Müller <hans@acme.example>",
        "container": "Inbox",
        "direction": "inbound",
        "path": f"2026/10/14/{key}/message.eml",
        "sha256": "ab" * 32,
        "size": 1234,
    }
    return Item(**(fields | kwargs))


def open_state(home):
    return State(home, {"mail": home / "mails", "notes": home / "notes"}, now=lambda: NOW)


@pytest.fixture
def state(tmp_path):
    with open_state(tmp_path) as s:
        yield s


def finish(state, item, status="done", **kwargs):
    return state.finish_run(
        state.start_run(item), item, status=status, outcome="success", cost_usd=0.5, turns=3, duration_ms=10, **kwargs
    )


def test_an_item_is_stored_with_a_short_id(state):
    added = state.add(item(), "Inbox", "17")
    assert len(added.id) == 12
    assert added.status == "new"
    assert added.downloaded_at == NOW.isoformat()
    found = state.find("mail", "a@example.com")
    assert found == added
    assert found.containers == ("Inbox",)


def test_each_archive_has_a_database_beside_it(tmp_path):
    assert database(tmp_path / "mails") == tmp_path / "mails.db"
    assert database(tmp_path / "mail.archive") == tmp_path / "mail.archive.db"
    with open_state(tmp_path):
        pass
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mails.db", "notes.db", "runs.db"]


def test_downloads_and_runs_are_kept_apart(tmp_path):
    with open_state(tmp_path) as s:
        added = s.add(item(), "Inbox", "17")
        finish(s, added, report="mails/2026/10/14/x/report.md")
    downloads = sqlite3.connect(tmp_path / "mails.db")
    assert downloads.execute("SELECT key, status FROM items").fetchall() == [("a@example.com", "new")]
    runs = sqlite3.connect(tmp_path / "runs.db")
    assert runs.execute("SELECT item, status, attempts FROM outcomes").fetchall() == [(added.id, "done", 1)]
    downloads.close()
    runs.close()


def test_without_runs_db_every_item_is_as_downloaded(tmp_path):
    # Deleting runs.db (and runs/) starts the imports afresh: what the download, the skip rules and the owner
    # decided stays, and nothing is downloaded again.
    with open_state(tmp_path) as s:
        done = s.add(item("1"), "Inbox", "1")
        finish(s, done, report="mails/1/report.md")
        skipped = s.add(item("2", status="skipped", reason="bulk: List-Unsubscribe"), "Inbox", "2")
        finish(s, skipped, status="done")
        s.set_status(s.add(item("3"), "Inbox", "3"), "ignored")
        s.set_cursor("mail", "Inbox", "14:250")
    (tmp_path / "runs.db").unlink()
    with open_state(tmp_path) as s:
        found = {i.key: i for i in s.items("mail")}
        assert [(found[k].status, found[k].reason) for k in "123"] == [
            ("new", None),
            ("skipped", "bulk: List-Unsubscribe"),
            ("ignored", None),
        ]
        assert (found["1"].attempts, found["1"].cost_usd, found["1"].report, found["1"].processed_at) == (
            0,
            0,
            None,
            None,
        )
        assert s.runs(found["1"]) == []
        assert s.cursor("mail", "Inbox") == "14:250"


def test_the_same_item_in_another_folder_is_one_item_with_two_places(state):
    first = state.add(item(), "Inbox", "17")
    state.locate(first, "Archive", "3")
    state.locate(first, "Archive", "3")  # twice is once
    assert state.find("mail", "a@example.com").containers == ("Archive", "Inbox")
    assert state.known_refs("mail", "Archive") == {"3"}


def test_ids_that_clash_get_longer(state, monkeypatch):
    monkeypatch.setattr("glean.state.ID_LENGTH", 1)
    ids = {state.add(item(key=f"m{i}@x"), "Inbox", str(i)).id for i in range(40)}
    assert len(ids) == 40


def test_ids_are_unique_across_sources(state, monkeypatch):
    monkeypatch.setattr("glean.state.ID_LENGTH", 1)
    mail = {state.add(item(key=f"m{i}@x"), "Inbox", str(i)).id for i in range(20)}
    notes = {state.add(item(key=f"m{i}@x", source="notes"), "all", str(i)).id for i in range(20)}
    assert not mail & notes


def test_cursors_are_kept_per_source_and_folder(state):
    assert state.cursor("mail", "Inbox") is None
    state.set_cursor("mail", "Inbox", "14:200")
    state.set_cursor("mail", "Inbox", "14:250")
    assert state.cursor("mail", "Inbox") == "14:250"
    assert state.cursor("mail", "Sent") is None
    assert state.cursor("notes", "Inbox") is None
    assert state.last_download("mail") == NOW.isoformat()


def test_pending_takes_the_oldest_or_the_newest_and_returns_them_oldest_first(state):
    # Dates in different offsets: the order is by the moment, not by the text.
    state.add(item("1", "2026-10-14T09:00:00+02:00"), "Inbox", "1")
    state.add(item("2", "2026-10-14T08:30:00+00:00"), "Inbox", "2")  # 10:30 in +02:00
    state.add(item("3", "2026-10-14T10:00:00+02:00"), "Inbox", "3")
    state.add(item("4", "2026-10-15T10:00:00+02:00", status="skipped", reason="bulk: List-Unsubscribe"), "Inbox", "4")
    assert [i.key for i in state.pending("mail", 2)] == ["1", "3"]
    assert [i.key for i in state.pending("mail", 2, newest=True)] == ["3", "2"]
    assert [i.key for i in state.pending("mail", 10)] == ["1", "3", "2"]
    assert [i.key for i in state.pending("mail", 10, ("new", "skipped"))] == ["1", "3", "2", "4"]
    assert state.pending("notes", 10) == []


def test_get_finds_an_item_by_id_prefix_or_key(state):
    added = state.add(item(), "Inbox", "17")
    assert state.get("mail", added.id) == added
    assert state.get("mail", added.id[:6]) == added
    assert state.get("mail", "a@example.com") == added
    assert state.get("mail", "<A@Example.com>", normalize=lambda k: k.strip("<>").lower()) == added
    with pytest.raises(GleanError, match="no mail item"):
        state.get("mail", "zzzz")


def test_an_ambiguous_prefix_is_refused(state, monkeypatch):
    monkeypatch.setattr("glean.state.ID_LENGTH", 2)
    firsts = [state.add(item(key=f"m{i}@x"), "Inbox", str(i)).id[0] for i in range(40)]
    shared = next(c for c in firsts if firsts.count(c) > 1)  # 40 ids, 16 first digits
    with pytest.raises(GleanError, match="matches several"):
        state.get("mail", shared)


def test_a_run_is_recorded_on_the_item(state):
    added = state.add(item(), "Inbox", "17")
    run = state.start_run(added)
    done = state.finish_run(
        run,
        added,
        status="failed",
        outcome="error_max_turns",
        cost_usd=0.25,
        turns=60,
        duration_ms=1000,
        document="2026/10/14/x/a.md",
        report="mails/2026/10/14/x/report.md",
        reason="the agent ran out of turns",
    )
    assert (done.status, done.attempts, done.cost_usd, done.reason) == ("failed", 1, 0.25, "the agent ran out of turns")
    run = state.start_run(done)
    done = state.finish_run(run, done, status="done", outcome="success", cost_usd=0.5, turns=12, duration_ms=500)
    assert (done.status, done.attempts, done.cost_usd, done.reason) == ("done", 2, 0.75, None)
    assert done.processed_at == NOW.isoformat()
    assert (done.document, done.report) == ("2026/10/14/x/a.md", "mails/2026/10/14/x/report.md")  # from the first
    assert [r["outcome"] for r in state.runs(done)] == ["error_max_turns", "success"]


def test_a_run_makes_an_item_done_failed_or_skipped(state):
    added = state.add(item(), "Inbox", "17")
    with pytest.raises(ValueError, match="outcome"):
        finish(state, added, status="ignored")


def test_a_stopped_run_leaves_the_status(state):
    added = finish(state, state.add(item(), "Inbox", "17"), status="failed", reason="timeout")
    stopped = finish(state, added, status=None, reason="stopped with Ctrl+C")
    assert (stopped.status, stopped.attempts) == ("failed", 2)
    never = finish(state, state.add(item("b@x"), "Inbox", "18"), status=None, reason="stopped with Ctrl+C")
    assert (never.status, never.reason, never.attempts) == ("new", "stopped with Ctrl+C", 1)


def test_a_reset_item_is_new_and_keeps_its_runs(state):
    done = finish(state, state.add(item(status="skipped", reason="bulk: List-Id"), "Inbox", "17"))
    again = state.reset(done)
    assert (again.status, again.reason, again.attempts, again.wanted) == ("new", None, 1, None)  # a retry
    assert len(state.runs(again)) == 1


def test_an_item_reset_to_be_imported_keeps_why_until_a_run_records_it(state):
    # The review named an email a person wrote, which the first look had skipped: judged again, it was skipped again.
    skipped = finish(state, state.add(item(), "Inbox", "17"), status="skipped", reason="agent: skipped: a notice")
    wanted = state.reset(skipped, "the review: Anna Keller wrote it")
    assert (wanted.status, wanted.wanted) == ("new", "the review: Anna Keller wrote it")
    stopped = finish(state, wanted, status=None, reason="stopped with Ctrl+C")
    assert stopped.wanted == "the review: Anna Keller wrote it"
    failed = finish(state, stopped, status="failed", reason="timeout")
    assert failed.wanted == "the review: Anna Keller wrote it"  # tried again with --retry-failed, still wanted
    assert finish(state, failed).wanted is None
    assert state.reset(state.reset(failed, "x")).wanted is None  # a plain reset judges it afresh


def test_a_runs_db_of_glean_0_12_gains_what_is_new(tmp_path):
    db = sqlite3.connect(tmp_path / "runs.db")
    db.executescript(
        "CREATE TABLE outcomes (source TEXT NOT NULL, item TEXT NOT NULL, status TEXT, reason TEXT, attempts INTEGER "
        "NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0, report TEXT, processed_at TEXT, PRIMARY KEY (source, "
        "item)); INSERT INTO outcomes VALUES ('mail', 'x', 'done', NULL, 1, 0.5, 'r.md', '2026-10-01');"
        "PRAGMA user_version = 1;"
    )
    db.close()
    with open_state(tmp_path) as s:
        added = s.add(item(), "Inbox", "17")
        assert s.reset(added, "the owner").wanted == "the owner"
        assert tuple(s.db.execute("SELECT status, cost_usd FROM outcomes WHERE item = 'x'").fetchone()) == ("done", 0.5)
    with open_state(tmp_path) as s:  # once only
        assert s.db.execute("PRAGMA main.user_version").fetchone()[0] == 2


def test_an_ignored_item_stays_ignored_whatever_its_runs_made_of_it(state):
    done = finish(state, state.add(item(), "Inbox", "17"))
    assert state.set_status(done, "ignored").status == "ignored"


def test_an_outcome_without_a_run(state):
    added = state.add(item(), "Inbox", "17")
    failed = state.set_outcome(added, "failed", "its document couldn't be written: OSError: x")
    assert (failed.status, failed.reason, failed.attempts) == (
        "failed",
        "its document couldn't be written: OSError: x",
        0,
    )
    with pytest.raises(ValueError, match="outcome"):
        state.set_outcome(added, "new")


def test_statuses_can_be_set_and_counted(state):
    a = state.add(item("1"), "Inbox", "1")
    state.add(item("2"), "Inbox", "2")
    state.add(item("3", status="skipped", reason="bulk"), "Inbox", "3")
    assert state.set_status(a, "ignored", "by hand").status == "ignored"
    with pytest.raises(ValueError, match="status"):
        state.set_status(a, "done")  # what a run makes of an item is the run's to say
    assert state.counts() == {"mail": {"new": 1, "skipped": 1, "ignored": 1}}
    assert [i.key for i in state.items("mail", "new")] == ["2"]
    assert state.total_cost("mail") == 0


def test_a_source_without_an_archive_is_refused(state):
    with pytest.raises(ValueError, match="no archive for the source 'other'"):
        state.add(item(source="other"), "Inbox", "1")


@pytest.mark.parametrize("name", ["main", "temp", "a b", 'x"y'])
def test_source_names_must_be_plain(tmp_path, name):
    with pytest.raises(ValueError, match="source name"):
        State(tmp_path, {name: tmp_path / "x"})


@pytest.mark.parametrize("name", ["runs.db", "mails.db"])
def test_a_database_from_a_newer_glean_is_refused(tmp_path, name):
    db = sqlite3.connect(tmp_path / name)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(GleanError, match=rf"{name} was written by a newer glean"):
        open_state(tmp_path)


@pytest.mark.parametrize("name", ["runs.db", "mails.db"])
def test_a_file_that_is_no_database_is_named(tmp_path, name):
    (tmp_path / name).write_bytes(b"not a database" * 200)
    with pytest.raises(GleanError, match=rf"{name} isn't a glean database"):
        open_state(tmp_path)


def test_a_home_of_glean_0_3_is_refused(tmp_path):
    (tmp_path / "state.db").write_bytes(b"")
    with pytest.raises(GleanError, match=r"state\.db.*glean 0\.3.*start a new home"):
        open_state(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["state.db"]


def test_a_home_without_sources_holds_nothing(tmp_path):
    # `glean status` before any source is set up
    with State(tmp_path, {}) as s:
        assert (s.counts(), s.items("mail"), s.pending("mail", 5)) == ({}, [], [])


def test_the_folder_is_created(tmp_path):
    with open_state(tmp_path / "new") as s:
        assert s.counts() == {}


@pytest.mark.parametrize("ref", ["_", "%", "a_", "%%"])
def test_wildcards_in_an_id_are_plain_characters(state, ref):
    for i in range(3):
        state.add(item(key=f"w{i}@x"), "Inbox", str(i))
    with pytest.raises(GleanError, match="no mail item"):
        state.get("mail", ref)


def test_contacts_are_the_addresses_written_to(state):
    assert state.contacts("mail") == set()
    state.add_contacts("mail", ["Hans@Acme.example", "lea@acme.example", "hans@acme.example"])
    state.add_contacts("notes", ["x@y.example"])
    assert state.contacts("mail") == {"hans@acme.example", "lea@acme.example"}


def test_an_items_places_are_found_by_index(state):
    # Each item read looks up its places: without an index, reading every item scans all places once per item.
    plan = state.db.execute('EXPLAIN QUERY PLAN SELECT DISTINCT container FROM "mail".locations WHERE item = ?', ("x",))
    assert any("USING" in row[-1] and "INDEX" in row[-1] for row in plan)


def test_reading_many_items_reads_their_places_at_once(state):
    # A query per item made every download's and upgrade's pass over a large mailbox slow.
    for n in range(30):
        added = state.add(item(f"m{n}@x"), "Inbox", str(n))
        state.locate(added, "Archive", str(n))
    queries = []
    state.db.set_trace_callback(queries.append)
    try:
        found = state.items("mail")
        waiting = state.pending("mail", 5)
    finally:
        state.db.set_trace_callback(None)
    assert len(queries) <= 4
    assert {i.containers for i in found + waiting} == {("Archive", "Inbox")}


def test_marks_are_kept_per_source_and_name(state):
    assert state.mark("mail", "rules") is None
    state.set_mark("mail", "rules", "2")
    state.set_mark("mail", "rules", "3")
    assert state.mark("mail", "rules") == "3"
    assert state.mark("notes", "rules") is None
