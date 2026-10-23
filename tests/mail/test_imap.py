"""Reading a mailbox over IMAP: read-only commands only, folders with any name, new messages since a cursor."""

import base64
import datetime
import imaplib

import pytest

from glean.connectors.mail import imap
from glean.connectors.mail.mailbox import Header
from glean.errors import GleanError

from . import samples
from .fake_imap import READ_ONLY, FakeImap


def mailbox(**kwargs):
    folders = {
        "Inbox": {"validity": 14, "messages": {1: samples.plain(), 2: samples.with_attachments()}},
        "Sent": {"flags": "\\Sent", "validity": 7, "messages": {5: samples.plain(message_id="<sent@x>")}},
        "Gelöscht": {"flags": "\\Trash", "validity": 3, "messages": {}, "wire": "Gel&APY-scht"},
        "Sent Items": {"validity": 2, "messages": {}},
        "Literal": {"validity": 1, "messages": {}, "literal": True},
    }
    server = FakeImap(folders, **kwargs)
    return server, imap.Imap("imap.example.com", 993, "me@example.com", lambda: "TOKEN", connect=server)


def test_sign_in_is_xoauth2():
    server, _ = mailbox()
    assert server.address == ("imap.example.com", 993, imap.TIMEOUT)
    assert server.auth == ("XOAUTH2", b"user=me@example.com\x01auth=Bearer TOKEN\x01\x01")


def test_a_refused_sign_in_says_to_log_in_again():
    with pytest.raises(GleanError, match="glean mail login"):
        mailbox(refuse_auth=True)


def test_an_unreachable_server_is_named():
    def unreachable(host, port, timeout):
        raise OSError("getaddrinfo failed")

    with pytest.raises(GleanError, match=r"can't reach imap\.example\.com:993"):
        imap.Imap("imap.example.com", 993, "me@example.com", lambda: "T", connect=unreachable)


def test_folders_with_their_flags_and_names():
    _, box = mailbox()
    folders = {f.name: f for f in box.folders()}
    assert set(folders) == {"Inbox", "Sent", "Gelöscht", "Sent Items", "Literal"}
    assert "\\sent" in folders["Sent"].flags
    assert folders["Gelöscht"].wire == "Gel&APY-scht"


def test_scan_lists_every_message_then_only_new_ones():
    server, box = mailbox()
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, cursor = box.scan(inbox, None)
    assert cursor == "14:2"
    assert [h.ref for h in headers] == ["14:1", "14:2"]
    assert headers[0].message_id == "<CAF1234@mail.acme.example>"
    assert headers[0].size == len(samples.plain())
    assert headers[0].received == datetime.datetime(
        2026, 10, 4, 9, 32, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
    )
    assert box.scan(inbox, cursor) == ([], "14:2")  # "3:*" matches the last message; it isn't new
    server.folders["Inbox"]["messages"][3] = samples.html_only()
    headers, cursor = box.scan(inbox, cursor)
    assert ([h.ref for h in headers], cursor) == (["14:3"], "14:3")


def test_a_new_uidvalidity_scans_the_folder_again():
    _, box = mailbox()
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, _ = box.scan(inbox, "13:2")
    assert [h.ref for h in headers] == ["14:1", "14:2"]


def test_an_empty_folder():
    _, box = mailbox()
    trash = next(f for f in box.folders() if f.name == "Gelöscht")
    assert box.scan(trash, None) == ([], "3:0")


@pytest.mark.parametrize("uid_last", [False, True])
def test_fetch_gives_whole_messages_in_batches(uid_last, monkeypatch):
    monkeypatch.setattr(imap, "BODY_BATCH_COUNT", 1)
    server, box = mailbox(uid_last=uid_last)
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, _ = box.scan(inbox, None)
    stale = Header("13:9", None, 10, None)
    fetched = dict(box.fetch(inbox, [*headers, stale]))
    stored = server.folders["Inbox"]["messages"]
    assert fetched == {"14:1": stored[1], "14:2": stored[2]}
    assert server.commands.count("UID FETCH") == 3  # headers once, then one message per batch


def test_fetch_batches_by_size(monkeypatch):
    monkeypatch.setattr(imap, "BODY_BATCH_BYTES", 10)
    server, box = mailbox()
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, _ = box.scan(inbox, None)
    assert len(dict(box.fetch(inbox, headers))) == 2
    assert server.commands.count("UID FETCH") == 3


def test_only_read_only_commands_are_sent():
    server, box = mailbox()
    for folder in box.folders():
        headers, _ = box.scan(folder, None)
        list(box.fetch(folder, headers))
    box.close()
    assert set(server.commands) <= READ_ONLY
    assert "EXAMINE" in server.commands


@pytest.mark.parametrize("refused, call", [("LIST", "folders"), ("EXAMINE", "scan")])
def test_a_refused_command_is_an_error(refused, call):
    _, box = mailbox(refuse=[refused])
    with pytest.raises(GleanError, match="refused"):
        box.folders() if call == "folders" else box.scan(imap.Folder("Inbox", wire="Inbox"), None)


def test_a_folder_without_uidvalidity_is_refused():
    server, box = mailbox()
    server.folders["Inbox"]["validity"] = None
    with pytest.raises(GleanError, match="no UIDVALIDITY for Inbox"):
        box.scan(imap.Folder("Inbox", wire="Inbox"), None)


def test_closing_twice_is_fine():
    server, box = mailbox()
    box.close()
    server.logout = lambda: (_ for _ in ()).throw(OSError("closed"))
    box.close()


@pytest.mark.parametrize(
    "name, decoded",
    [("Entw&APw-rfe", "Entwürfe"), ("A&-B", "A&B"), ("Inbox", "Inbox"), ("&ZeVnLIqe-", "日本語")],
)
def test_modified_utf7_folder_names(name, decoded):
    assert imap.decode_folder_name(name) == decoded


def test_quote_escapes():
    assert imap.quote('a "b" \\c') == '"a \\"b\\" \\\\c"'


@pytest.mark.parametrize(
    "text, expected",
    [
        ("14-Oct-2026 09:32:00 +0200", "2026-10-14T09:32:00+02:00"),
        (" 4-Oct-2026 09:32:00 -0500", "2026-10-04T09:32:00-05:00"),
        ("14-Okt-2026 09:32:00 +0200", None),
        ("yesterday", None),
    ],
)
def test_internal_dates(text, expected):
    found = imap.internal_date(text)
    assert (found.isoformat() if found else None) == expected


def test_xoauth2_is_the_sasl_string():
    assert base64.b64encode(imap.xoauth2("a@b", "t")) == base64.b64encode(b"user=a@b\x01auth=Bearer t\x01\x01")


def test_error_text_from_any_shape():
    assert imap._text([b"a", None, "b"]) == "a b"
    assert imap._text(b"x") == "x"
    assert imap._text(3) == "3"


def test_responses_without_a_uid_are_passed_over():
    server, box = mailbox()
    server.no_uid = {2}
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, cursor = box.scan(inbox, None)
    assert [h.ref for h in headers] == ["14:1"]
    assert cursor == "14:1"  # not past the message it couldn't place: the next scan sees it again
    assert list(box.fetch(inbox, [imap.Header("14:2", None, 1, None)])) == []


def test_fetch_responses_in_any_shape():
    data = [b"stray", None, (b"1 (UID 4 BODY[] {1}", b"x"), b" FLAGS ())", None]
    assert imap._responses(data) == [[b"1 (UID 4 BODY[] {1}  FLAGS ())", b"x"]]


@pytest.mark.parametrize("error", [imaplib.IMAP4.abort("socket error: EOF"), TimeoutError("timed out")])
def test_a_connection_that_breaks_says_to_download_again(error):
    server, box = mailbox()
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, _ = box.scan(inbox, None)

    def broken(*args):
        raise error

    server.uid = broken
    with pytest.raises(
        GleanError, match=r"broke while trying to fetch messages in Inbox \(.+\): run `glean mail download` again"
    ):
        list(box.fetch(inbox, headers))


def test_a_connection_that_breaks_while_signing_in():
    server = FakeImap({})

    def broken(mechanism, callback):
        raise ConnectionResetError("reset by peer")

    server.authenticate = broken
    with pytest.raises(GleanError, match="connection to the IMAP server broke"):
        imap.Imap("h", 993, "u", lambda: "T", connect=server)


@pytest.mark.parametrize("name", ["Bad&AB*-name", "&Jjo!-", "&2D3-"])  # not base64; a lone surrogate
def test_folder_names_that_are_not_valid_utf7_stay_as_they_are(name):
    assert imap.decode_folder_name(name) == name


def test_a_command_the_server_rejects_is_refused():
    server, box = mailbox()
    inbox = next(f for f in box.folders() if f.name == "Inbox")

    def bad(*args):
        raise imaplib.IMAP4.error("UID command error: BAD [b'Command Argument Error. 12']")

    server.uid = bad
    with pytest.raises(GleanError, match=r"refused to search Inbox: UID command error: BAD"):
        box.scan(inbox, None)


def test_a_search_answered_in_several_lines():
    server, box = mailbox()
    server.folders["Inbox"]["messages"][3] = samples.forwarded()
    real = server.uid

    def split(command, *args):
        typ, data = real(command, *args)
        if command == "SEARCH":
            numbers = data[0].split()
            return typ, [b" ".join(numbers[:1]), b" ".join(numbers[1:])]  # two "* SEARCH" lines
        return typ, data

    server.uid = split
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, cursor = box.scan(inbox, None)
    assert [h.ref for h in headers] == ["14:1", "14:2", "14:3"]
    assert cursor == "14:3"
