"""Each download, run and review keeps a log of its own."""

import datetime

import pytest

from glean import console, logs

NOW = datetime.datetime(2026, 10, 23, 21, 5, 9)


def interrupted_run(home):
    with logs.kept(home, "mail-run", NOW):
        console.say("reviewing 2 imports")
        console.show("done    a1b2c3  Offer")
        raise KeyboardInterrupt


def test_a_log_holds_what_glean_said_and_how_it_ended(tmp_path, capsys):
    with pytest.raises(KeyboardInterrupt):
        interrupted_run(tmp_path)
    (path,) = (tmp_path / "logs").glob("*.log")
    assert path == tmp_path / "logs" / "mail-run-2026-10-23_210509.log"
    lines = [line.split(" ", 2)[2] for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines == ["INFO reviewing 2 imports", "INFO done    a1b2c3  Offer", "ERROR stopped"]
    console.say("after")
    assert "after" not in path.read_text(encoding="utf-8")
    assert capsys.readouterr() == ("done    a1b2c3  Offer\n", "reviewing 2 imports\nafter\n")


def test_two_in_one_second_get_two_files(tmp_path):
    with logs.kept(tmp_path, "mail-run", NOW) as first, logs.kept(tmp_path, "mail-run", NOW) as second:
        pass
    assert (first.name, second.name) == ("mail-run-2026-10-23_210509.log", "mail-run-2026-10-23_210509-2.log")


def test_only_the_newest_of_a_kind_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "KEEP", 2)
    folder = tmp_path / "logs"
    folder.mkdir()
    for day in (1, 2, 3):
        (folder / f"mail-run-2026-10-0{day}_120000.log").write_text("x", encoding="utf-8")
    (folder / "mail-download-2026-10-01_120000.log").write_text("x", encoding="utf-8")
    with logs.kept(tmp_path, "mail-run", NOW):
        pass
    assert sorted(p.name for p in folder.iterdir()) == [
        "mail-download-2026-10-01_120000.log",
        "mail-run-2026-10-03_120000.log",
        "mail-run-2026-10-23_210509.log",
    ]
