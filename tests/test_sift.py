"""Keeping sift's collections in line: the collection that holds a folder, or one added; .siftignore kept; indexing."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from glean import sift
from glean.errors import GleanError

REAL_FIND = sift.Sift.find  # before the tests' fixture keeps every test from the real sift


def done(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class FakeSift:
    """The sift command, as far as glean uses it: status --json, add, index."""

    def __init__(self, collections=(), fail=None):
        self.collections, self.calls, self.fail = [dict(c) for c in collections], [], fail

    def __call__(self, cmd, **kwargs):
        args = cmd[1:]
        self.calls.append(args)
        if args[0] == self.fail:
            return done(stderr="usage: sift ...\nerror: something broke", returncode=2)
        if args[0] == "status":
            return done(json.dumps({"index": "x", "collections": self.collections}))
        if args[0] == "add":
            self.ignore_at_add = (sift.Path(args[1]) / ".siftignore").exists()
            self.collections.append({"name": args[3], "path": args[1]})
            return done(stderr=f"added collection {args[3]!r}; run `sift index` next")
        return done(stderr=f"{args[1]}: scanning\n{args[1]}: 3 files, 3 added, 0 changed, 0 removed (0.1s)\n")


def make(tmp_path, collections=(), fail=None):
    fake = FakeSift(collections, fail)
    return sift.Sift(["sift"], fake), fake


def test_a_folder_no_collection_holds_gets_one(tmp_path):
    s, fake = make(tmp_path, [{"name": "arxiv", "path": str(tmp_path / "papers")}])
    name, added = sift.collection(s, tmp_path / "home" / "mail", "mail", ("message.eml",))
    assert (name, added) == ("mail", True)
    assert ["add", str(tmp_path / "home" / "mail"), "--name", "mail", "--title-from", "name"] in fake.calls
    assert fake.ignore_at_add  # written before sift reads the folder
    assert "message.eml" in (tmp_path / "home" / "mail" / ".siftignore").read_text(encoding="utf-8")


def test_a_name_that_is_taken_gets_a_number(tmp_path):
    s, _ = make(tmp_path, [{"name": "mail", "path": str(tmp_path / "other")}, {"name": "mail-2", "path": "x"}])
    assert sift.collection(s, tmp_path / "mail", "mail") == ("mail-3", True)


def test_the_collection_that_holds_a_folder_is_used(tmp_path):
    home = tmp_path / "home"
    (home / "mail").mkdir(parents=True)
    s, fake = make(tmp_path, [{"name": "data", "path": str(home)}, {"name": "all", "path": str(tmp_path)}])
    assert sift.collection(s, home / "mail", "mail", ("message.eml",), owned=(home,)) == ("data", False)
    assert not any(call[0] == "add" for call in fake.calls)
    assert (home / ".siftignore").read_text(encoding="utf-8").endswith("message.eml\n")  # the nearer one's folder


def test_glean_writes_nothing_into_a_folder_it_does_not_keep(tmp_path):
    (tmp_path / "home" / "mail").mkdir(parents=True)
    s, _ = make(tmp_path, [{"name": "everything", "path": str(tmp_path)}])
    assert sift.collection(s, tmp_path / "home" / "mail", "mail", ("message.eml",), owned=(tmp_path / "home",)) == (
        "everything",
        False,
    )
    assert not (tmp_path / ".siftignore").exists()


def test_holder_is_the_nearest(tmp_path):
    collections = [{"name": "a", "path": str(tmp_path)}, {"name": "b", "path": str(tmp_path / "x")}]
    assert sift.holder(collections, tmp_path / "x" / "y")["name"] == "b"
    assert sift.holder(collections, tmp_path / "x")["name"] == "b"
    assert sift.holder(collections, tmp_path.parent) is None
    spelled_long = f"{tmp_path}{os.sep}.{os.sep}.{os.sep}."  # the same folder, a longer spelling
    collections = [{"name": "a", "path": spelled_long}, {"name": "b", "path": str(tmp_path / "x")}]
    assert sift.holder(collections, tmp_path / "x" / "y")["name"] == "b"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows compares paths without case")
def test_holder_ignores_case_on_windows(tmp_path):
    collections = [{"name": "a", "path": str(tmp_path).upper()}]
    assert sift.holder(collections, tmp_path / "x")["name"] == "a"


def test_siftignore_keeps_what_is_there_and_adds_once(tmp_path):
    (tmp_path / ".siftignore").write_text("drafts/\n*.tmp.md", encoding="utf-8-sig")
    assert sift.ignore(tmp_path, ["message.eml", "drafts/"]) == ["message.eml"]
    assert sift.ignore(tmp_path, ["message.eml"]) == []
    assert sift.ignore(tmp_path, ["other.eml"]) == ["other.eml"]
    text = (tmp_path / ".siftignore").read_text(encoding="utf-8")
    assert text == f"drafts/\n*.tmp.md\n\n{sift.IGNORE_HEAD}\nmessage.eml\nother.eml\n"


def test_index_says_what_sift_did(tmp_path):
    s, fake = make(tmp_path)
    assert s.index("mail") == ["mail: 3 files, 3 added, 0 changed, 0 removed (0.1s)"]
    assert fake.calls == [["index", "mail"]]


def test_a_failing_sift_command_is_named(tmp_path):
    s, _ = make(tmp_path, fail="add")
    with pytest.raises(
        GleanError, match=r"`sift add .* --name mail --title-from name` failed: .*error: something broke"
    ):
        sift.collection(s, tmp_path / "mail", "mail")


def test_a_sift_too_old_for_status_json(tmp_path):
    s = sift.Sift(["sift"], lambda cmd, **kwargs: done("arxiv: 3 documents"))
    with pytest.raises(GleanError, match=r"sift 1\.2 or newer"):
        s.collections()


@pytest.mark.parametrize("error", [OSError("not found"), subprocess.TimeoutExpired("sift", 1)])
def test_a_sift_that_does_not_run(tmp_path, error):
    def broken(cmd, **kwargs):
        raise error

    with pytest.raises(GleanError, match="`sift status` didn't finish"):
        sift.Sift(["sift"], broken).collections()


def test_find_takes_sift_from_path(monkeypatch):
    monkeypatch.setattr(sift.shutil, "which", lambda name: "/bin/sift")
    assert REAL_FIND().command == ["/bin/sift"]
    monkeypatch.setattr(sift.shutil, "which", lambda name: None)
    assert REAL_FIND() is None


def setting(tmp_path, add=True, vault=None):
    archive = tmp_path / "home" / "mail"
    cfg = SimpleNamespace(
        home=tmp_path / "home", orbit_url="http://o/mcp/write", orbit_vault=vault,
        sift=SimpleNamespace(add_collections=add, crm_collection="crm"),
    )  # fmt: skip
    connector = SimpleNamespace(name="mail", archive=archive, collection="mail", ignored=("message.eml",))
    return cfg, connector


def installed(monkeypatch, fake):
    monkeypatch.setattr(sift.Sift, "find", classmethod(lambda cls: sift.Sift(["sift"], fake)))


def test_keep_adds_both_collections(tmp_path, monkeypatch):
    fake, said = FakeSift(), []
    installed(monkeypatch, fake)
    cfg, connector = setting(tmp_path, vault=tmp_path / "vault")
    assert sift.keep(cfg, connector, said.append) == {"archive": "mail", "vault": "crm"}
    assert [c["name"] for c in fake.collections] == ["mail", "crm"]
    assert said[1] == f"sift: added the collection 'crm' for orbit's vault, {tmp_path / 'vault'}"
    assert sift.keep(cfg, connector, said.append) == {"archive": "mail", "vault": "crm"}  # once is enough
    assert len(said) == 2


def test_a_collection_of_the_whole_home_leaves_the_runs_out(tmp_path, monkeypatch):
    # The runs' transcripts are JSON Lines and their reports Markdown, which sift reads both.
    home = tmp_path / "home"
    (home / "mail").mkdir(parents=True)
    installed(
        monkeypatch, FakeSift([{"name": "data", "path": str(home)}, {"name": "crm", "path": str(tmp_path / "v")}])
    )
    sift.keep(*setting(tmp_path, vault=tmp_path / "v"), print)
    assert (home / ".siftignore").read_text(encoding="utf-8").splitlines()[-2:] == ["message.eml", "/runs/"]


def test_a_collection_of_the_archive_alone_holds_no_runs(tmp_path, monkeypatch):
    installed(monkeypatch, FakeSift())
    cfg, connector = setting(tmp_path, vault=tmp_path / "v")
    sift.keep(cfg, connector, print)
    assert (connector.archive / ".siftignore").read_text(encoding="utf-8").splitlines()[-1] == "message.eml"


def test_keep_asks_orbit_where_its_vault_is(tmp_path, monkeypatch):
    installed(monkeypatch, FakeSift([{"name": "crm-notes", "path": str(tmp_path / "v")}]))
    monkeypatch.setattr(sift.orbit, "vault", lambda url, timeout=None: tmp_path / "v")
    assert sift.keep(*setting(tmp_path), print)["vault"] == "crm-notes"


def test_keep_without_sift_or_when_told_not_to(tmp_path, monkeypatch):
    said = []
    assert sift.keep(*setting(tmp_path), said.append) == {"archive": "mail", "vault": "crm"}
    assert "sift isn't on PATH" in said[0]
    fake = FakeSift()
    installed(monkeypatch, fake)
    assert sift.keep(*setting(tmp_path, add=False), said.append)["archive"] == "mail"
    assert fake.calls == []


def test_keep_says_what_failed_and_goes_on(tmp_path, monkeypatch):
    installed(monkeypatch, FakeSift(fail="status"))
    said = []
    assert sift.keep(*setting(tmp_path), said.append, index=True) == {"archive": "mail", "vault": "crm"}
    assert said[0].startswith("warning: `sift status --json` failed")


def test_check_names_the_collections(tmp_path, monkeypatch):
    cfg, connector = setting(tmp_path, vault=tmp_path / "vault")
    assert sift.check(cfg, connector) == [(False, "sift: the sift command isn't on PATH")]
    installed(monkeypatch, FakeSift([{"name": "data", "path": str(tmp_path / "home")}]))
    assert sift.check(cfg, connector) == [
        (True, f"sift: the collection 'data' holds the mail archive, {connector.archive}"),
        (
            True,
            f"sift: no collection holds orbit's vault, {tmp_path / 'vault'}: "
            "glean adds one with the next download or run",
        ),
    ]
    cfg.sift.add_collections, cfg.orbit_vault = False, None
    lines = sift.check(cfg, connector)
    assert lines[1] == (False, "sift: orbit didn't say where its vault is; set [orbit] vault in glean.toml")
    installed(monkeypatch, FakeSift(fail="status"))
    assert sift.check(cfg, connector)[0][0] is False


def test_check_when_glean_is_not_to_add_collections(tmp_path, monkeypatch):
    cfg, connector = setting(tmp_path, add=False, vault=tmp_path / "vault")
    installed(monkeypatch, FakeSift())
    assert sift.check(cfg, connector)[0] == (
        False,
        f"sift: no collection holds the mail archive, {connector.archive}: add one",
    )
