"""The archive: a folder per item by date, file names Windows accepts, unique names, atomic writes, front matter."""

import datetime
import json

import pytest

from glean import archive

TZ = datetime.timezone(datetime.timedelta(hours=2))


def test_an_item_folder_is_dated():
    when = datetime.datetime(2026, 3, 4, 23, 30, tzinfo=TZ)
    assert archive.item_folder(archive.Path("/a"), when, "3f2a9c1b7d4e") == archive.Path("/a/2026/03/04/3f2a9c1b7d4e")


@pytest.mark.parametrize(
    "text, name",
    [
        ("Re: Offer / weekend on-call?", "Re Offer weekend on-call"),
        ('a<b>c:d"e/f\\g|h?i*j', "a b c d e f g h i j"),
        ("  trailing dots... ", "trailing dots"),
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),
        ("LPT1", "_LPT1"),
        ("tab\there\x00and\x1fcontrol", "tab here and control"),
        ("", "untitled"),
        ("???", "untitled"),
        ("Grüße aus Zürich", "Grüße aus Zürich"),
    ],
)
def test_safe_names(text, name):
    assert archive.safe_name(text) == name


def test_a_long_name_is_cut_at_a_word_and_keeps_its_extension():
    name = archive.safe_name("word " * 60 + ".pdf", limit=40)
    assert len(name) <= 40
    assert name.endswith("word.pdf")
    assert archive.safe_name("x" * 100, limit=20) == "x" * 20


def test_unique_names_count_up_case_blind():
    taken = {"offer.pdf", "offer (2).pdf"}
    assert archive.unique_name("Offer.pdf", taken) == "Offer (3).pdf"
    assert archive.unique_name("notes", set()) == "notes"
    assert archive.unique_name("notes", {"notes"}) == "notes (2)"


def test_write_atomic_replaces_whole_files(tmp_path):
    path = tmp_path / "a" / "b.txt"
    archive.write_atomic(path, b"one")
    archive.write_atomic(path, b"two")
    assert path.read_bytes() == b"two"
    assert [p.name for p in path.parent.iterdir()] == ["b.txt"]


def test_write_atomic_leaves_nothing_behind_on_failure(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(archive.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        archive.write_atomic(tmp_path / "c.txt", b"x")
    assert list(tmp_path.iterdir()) == []


def test_front_matter_is_flat_json_values():
    text = archive.front_matter(
        {"type": "email", "subject": 'Re: "Offer" \u2013 50%', "to": ["Hans <h@x.example>", "b@y.example"], "n": 3,
         "in_reply_to": None, "empty": []}
    )  # fmt: skip
    lines = text.splitlines()
    assert lines[0] == lines[-1] == "---"
    pairs = dict(line.split(": ", 1) for line in lines[1:-1])
    assert json.loads(pairs["subject"]) == 'Re: "Offer" \u2013 50%'
    assert json.loads(pairs["to"]) == ["Hans <h@x.example>", "b@y.example"]
    assert pairs["n"] == "3"
    assert pairs["in_reply_to"] == "null"
    assert pairs["empty"] == "[]"
    assert "\u2013" in text  # not escaped: the file stays readable


def test_relative_paths_use_forward_slashes(tmp_path):
    assert archive.relative(tmp_path, tmp_path / "2026" / "a.md") == "2026/a.md"


@pytest.mark.parametrize(
    "text, name",
    [
        ("../../etc/passwd", "etc passwd"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("CONIN$", "_CONIN$"),
        ("conout$.txt", "_conout$.txt"),
    ],
)
def test_names_that_try_to_leave_the_folder_or_are_reserved(text, name):
    assert archive.safe_name(text) == name


def test_write_atomic_waits_for_a_file_another_program_holds(tmp_path, monkeypatch):
    real, calls = archive.os.replace, []

    def busy(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("the file is being used by another process")
        real(src, dst)

    monkeypatch.setattr(archive.os, "replace", busy)
    monkeypatch.setattr(archive, "REPLACE_WAIT", 0.001)
    archive.write_atomic(tmp_path / "d.txt", b"x")
    assert (tmp_path / "d.txt").read_bytes() == b"x"
    assert len(calls) == 3


def test_write_atomic_gives_up_on_a_file_held_for_good(tmp_path, monkeypatch):
    def busy(src, dst):
        raise PermissionError("the file is being used by another process")

    monkeypatch.setattr(archive.os, "replace", busy)
    monkeypatch.setattr(archive, "REPLACE_WAIT", 0.001)
    with pytest.raises(PermissionError):
        archive.write_atomic(tmp_path / "e.txt", b"x")
    assert list(tmp_path.iterdir()) == []


def test_local_time_of_a_date_the_system_cannot_convert():
    class Odd(datetime.datetime):
        def astimezone(self, tz=None):
            raise OSError(22, "Invalid argument")  # Windows, for dates before 1970

    odd = Odd(1900, 1, 1, tzinfo=TZ)
    assert archive.local_time(odd) is odd
    assert archive.local_time(datetime.datetime(2026, 1, 1, tzinfo=TZ)).tzinfo is not None
