"""The mail connector: which folders it reads, downloading into the archive and the state database, documents."""

import datetime
import hashlib
import os
from types import SimpleNamespace

import pytest

from glean import config
from glean.connectors.mail import GUIDE, VERSION, Mail, imap
from glean.connectors.mail import markdown as markdown_module
from glean.connectors.mail import message as mail_message
from glean.connectors.mail.mailbox import EmlFolder, Folder
from glean.errors import GleanError
from glean.state import State

from ..conftest import NOW
from . import samples
from .fake_imap import READ_ONLY, FakeImap


def connector(tmp_path, extra="", eml=False):
    text = '[mail]\naddress = "tobias@example.com"\nclient_id = "cid"\narchive = "archive"\n' + extra
    if eml:
        text += 'eml_folder = "eml"\n'
    path = tmp_path / "glean.toml"
    path.write_text(text, encoding="utf-8")
    said = []
    return Mail(config.load(path).sources["mail"], tmp_path, said.append), said


@pytest.fixture
def state(tmp_path):
    with State(tmp_path, {"mail": tmp_path / "archive"}, now=lambda: NOW) as s:
        yield s


def eml_folder(tmp_path):
    root = tmp_path / "eml"
    (root / "Sent").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "a.eml").write_bytes(samples.plain())
    (root / "b.eml").write_bytes(samples.html_only())
    (root / "Sent" / "c.eml").write_bytes(samples.plain(message_id="<mine@x>", sender="tobias@example.com"))
    (root / "Sent" / "d.eml").write_bytes(samples.plain())  # the same email in another folder
    return root


def test_folders_chosen_by_flags_and_names(tmp_path):
    mail, _ = connector(tmp_path)
    folders = [
        Folder("Inbox"),
        Folder("Junk", frozenset({"\\junk"})),
        Folder("Papierkorb", frozenset({"\\trash"})),
        Folder("Entwürfe", frozenset({"\\drafts"})),
        Folder("Deleted"),
        Folder("[Gmail]", frozenset({"\\noselect"})),
        Folder("Archive"),
    ]
    assert [f.name for f in mail.chosen(folders)] == ["Inbox", "Archive"]
    only, _ = connector(tmp_path, 'folders = ["junk", "[Gmail]"]\n')
    assert [f.name for f in only.chosen(folders)] == ["Junk"]


def test_download_from_an_eml_folder(tmp_path, state):
    eml_folder(tmp_path)
    mail, said = connector(tmp_path, eml=True)
    report = mail.download(state)
    assert (report.new, report.skipped, report.known) == (3, 0, 1)
    assert report.places == {"Inbox": 2, "Sent": 1}
    assert any("downloading 2 emails" in s for s in said)
    hans = state.find("mail", "caf1234@mail.acme.example")
    assert hans.containers == ("Inbox", "Sent")
    assert (hans.direction, hans.status, hans.title) == ("inbound", "new", "Offerte Wartungsvertrag 2027")
    assert hans.author == "Hans Müller <hans.mueller@acme.example>"
    assert hans.date == "2026-10-14T09:32:00+02:00"
    original = mail.archive / hans.path
    local = datetime.datetime.fromisoformat(hans.date).astimezone()
    assert original == mail.archive / f"{local:%Y/%m/%d}" / hans.id / "message.eml"
    assert original.read_bytes() == samples.plain()
    assert hans.sha256 == hashlib.sha256(samples.plain()).hexdigest()
    news = state.find("mail", "html-1@shop.example")
    assert (news.status, news.reason) == ("new", None)  # a newsletter: the agent judges it
    assert state.find("mail", "mine@x").direction == "outbound"
    again = mail.download(state)
    assert (again.new, again.known) == (0, 0)  # every file known by its place


def test_download_over_imap_reads_only_and_continues_where_it_stopped(tmp_path, state, monkeypatch):
    server = FakeImap(
        {
            "Inbox": {"validity": 1, "messages": {1: samples.plain(), 2: samples.with_attachments()}},
            "Sent": {"flags": "\\Sent", "validity": 1, "messages": {9: samples.plain()}},
            "Junk": {"flags": "\\Junk", "validity": 1, "messages": {1: samples.html_only()}},
        }
    )
    mail, _ = connector(tmp_path)
    monkeypatch.setattr(mail, "mailbox", lambda: imap.Imap("h", 993, "tobias@example.com", lambda: "T", server))
    report = mail.download(state)
    assert (report.new, report.known) == (2, 1)  # the copy in Sent is known by its Message-ID before fetching
    assert state.find("mail", "caf1234@mail.acme.example").containers == ("Inbox", "Sent")
    assert state.find("mail", "html-1@shop.example") is None  # Junk isn't read
    assert set(server.commands) <= READ_ONLY
    server.folders["Inbox"]["messages"][3] = samples.forwarded()
    report = mail.download(state)
    assert (report.new, report.known) == (1, 0)
    assert state.cursor("mail", "Inbox") == "1:3"


def dated_eml_folder(tmp_path):
    """Five emails, day1 to day5, each arrived a day after the one before; the odd days in Inbox, the even in Sent."""
    root = tmp_path / "eml"
    (root / "Sent").mkdir(parents=True)
    for day in range(1, 6):
        path = (root if day % 2 else root / "Sent") / f"{day}.eml"
        path.write_bytes(samples.plain(message_id=f"<day{day}@x>"))
        stamp = datetime.datetime(2026, 10, day, tzinfo=datetime.UTC).timestamp()
        os.utime(path, (stamp, stamp))


def downloaded(state):
    return [d for d in range(1, 6) if state.find("mail", f"day{d}@x")]


def test_a_download_of_the_oldest_n_takes_them_across_folders_and_the_next_continues(tmp_path, state):
    dated_eml_folder(tmp_path)
    mail, said = connector(tmp_path, eml=True)
    report = mail.download(state, oldest=2)
    assert (report.new, report.waiting) == (2, 3)
    assert downloaded(state) == [1, 2]
    assert "Inbox: downloading 1 email" in said
    report = mail.download(state, oldest=2)
    assert (report.new, report.waiting) == (2, 1)
    assert downloaded(state) == [1, 2, 3, 4]
    report = mail.download(state)
    assert (report.new, report.waiting) == (1, 0)


def test_a_download_of_the_latest_n_takes_the_newest(tmp_path, state):
    dated_eml_folder(tmp_path)
    mail, _ = connector(tmp_path, eml=True)
    report = mail.download(state, latest=3)
    assert (report.new, report.waiting) == (3, 2)
    assert downloaded(state) == [3, 4, 5]


def test_a_limited_download_over_imap_keeps_the_cursor_of_a_folder_with_mail_left(tmp_path, state, monkeypatch):
    first, second, third = (samples.plain(message_id=f"<day{d}@x>") for d in (1, 2, 3))
    server = FakeImap(
        {
            "Inbox": {"validity": 1, "messages": {1: first, 2: second, 3: third}},
            "Sent": {"flags": "\\Sent", "validity": 1, "messages": {9: first}},
        }
    )
    mail, _ = connector(tmp_path)
    monkeypatch.setattr(mail, "mailbox", lambda: imap.Imap("h", 993, "tobias@example.com", lambda: "T", server))
    report = mail.download(state, oldest=2)
    assert (report.new, report.known, report.waiting) == (2, 1, 1)  # Sent's copy of the first doesn't count
    assert state.find("mail", "day1@x").containers == ("Inbox", "Sent")
    assert (state.cursor("mail", "Inbox"), state.cursor("mail", "Sent")) == (None, "1:9")
    report = mail.download(state, oldest=2)
    assert (report.new, report.known, report.waiting) == (1, 0, 0)
    assert state.cursor("mail", "Inbox") == "1:3"


def test_a_message_found_again_after_a_new_uidvalidity_is_known(tmp_path, state, monkeypatch):
    server = FakeImap({"Inbox": {"validity": 1, "messages": {1: samples.forwarded()}}})
    mail, _ = connector(tmp_path)
    monkeypatch.setattr(mail, "mailbox", lambda: imap.Imap("h", 993, "u", lambda: "T", server))
    mail.download(state)
    server.folders["Inbox"]["validity"] = 2
    report = mail.download(state)
    assert (report.new, report.known) == (0, 1)
    assert state.known_refs("mail", "Inbox") == {"1:1", "2:1"}


def test_an_email_without_a_date_is_filed_by_when_it_arrived(tmp_path, state):
    root = tmp_path / "eml"
    root.mkdir()
    (root / "x.eml").write_bytes(b"Message-ID: <nodate@x>\r\nFrom: a@b.example\r\n\r\nHi\r\n")
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    found = state.find("mail", "nodate@x")
    assert found.date  # the file's time
    assert (mail.archive / found.path).is_file()


def test_progress_is_reported(tmp_path, state, monkeypatch):
    monkeypatch.setattr("glean.connectors.mail.PROGRESS_EVERY", 1)
    eml_folder(tmp_path)
    mail, said = connector(tmp_path, eml=True)
    mail.download(state)
    assert "Inbox: 1 of 2" in said


def test_the_document_of_a_downloaded_email(tmp_path, state):
    eml_folder(tmp_path)
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    doc = mail.document(state.find("mail", "caf1234@mail.acme.example"))
    assert doc.markdown.parent == doc.original.parent
    assert 'folders: ["Inbox", "Sent"]' in doc.text
    assert "Anbei die Offerte." in doc.text
    assert doc.addresses == ("hans.mueller@acme.example", "tobias@example.com")  # the interaction names them all


def test_instructions_for_the_agent(tmp_path):
    mail, _ = connector(tmp_path)
    text = mail.instructions()
    assert "log_interaction" in text
    assert "message_id" in text


def test_the_instructions_leave_out_what_the_email_lacks(tmp_path):
    # For an email without a subject, the agent wrote `"subject": ,`, which isn't JSON, and called again and again.
    mail, _ = connector(tmp_path)
    assert "leave out those the front matter has as null, such as the subject of an email without one" in (
        " ".join(mail.instructions().split())
    )


def test_the_instructions_give_an_offices_mailbox_to_its_organization(tmp_path):
    # A shop's branch mailbox became a person named after the branch's town.
    mail, _ = connector(tmp_path)
    assert "Not people: role addresses (info@, support@, …), a mailbox named after a place, a team or an office" in (
        " ".join(mail.instructions().split())
    )


def test_the_instructions_name_the_owners_addresses(tmp_path):
    # Agents linked the owner by address, or by a name of their own, which orbit's Me.md may not have.
    mail, _ = connector(tmp_path, 'my_addresses = ["Tobias.Privat@mail.example"]\n')
    assert (
        "The owner's addresses are tobias@example.com and tobias.privat@mail.example: wherever one appears, that is "
        "Me, orbit's note of the owner"
    ) in mail.instructions()


def test_keys_as_people_write_them(tmp_path):
    mail, _ = connector(tmp_path)
    assert mail.key("<ABC@x.example>") == "abc@x.example"
    assert mail.key("3f2a") == "3f2a"


def test_a_missing_eml_folder_is_named(tmp_path):
    mail, _ = connector(tmp_path, eml=True)
    with pytest.raises(GleanError, match="isn't a folder"):
        mail.mailbox()
    assert mail.check() == [(False, f"mail: reads .eml files from {tmp_path / 'eml'}, which is missing")]
    (tmp_path / "eml").mkdir()
    assert isinstance(mail.mailbox(), EmlFolder)
    assert mail.check()[0][0] is True


class FakeApp:
    def __init__(self, accounts=(), result=None, fail=False):
        self.accounts, self.result, self.fail, self.calls = list(accounts), result, fail, []

    def get_accounts(self, username=None):
        if self.fail:
            raise ValueError("cache unreadable")
        return [a for a in self.accounts if username in (None, a["username"])]

    def acquire_token_interactive(self, scopes, **kwargs):
        self.calls.append(("interactive", kwargs))
        return self.result

    def acquire_token_silent(self, scopes, account):
        return self.result

    def remove_account(self, account):
        self.accounts.remove(account)


def test_check_reports_the_sign_in(tmp_path, monkeypatch):
    mail, _ = connector(tmp_path)
    app = FakeApp()
    monkeypatch.setattr(mail, "account", lambda: Mail.account(mail, app))
    assert mail.check() == [(False, "mail: not signed in: run `glean mail login`")]
    app.accounts.append({"username": "tobias@example.com"})
    assert mail.check() == [(True, "mail: signed in as tobias@example.com")]
    app.fail = True
    assert mail.check()[0][0] is False


def test_the_imap_mailbox_signs_in_with_the_accounts_token(tmp_path, monkeypatch):
    mail, _ = connector(tmp_path)
    app = FakeApp([{"username": "tobias@example.com"}], {"access_token": "AT"})
    monkeypatch.setattr(mail, "account", lambda: Mail.account(mail, app))
    server = FakeImap({})
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", server)
    box = mail.mailbox()
    assert server.auth == ("XOAUTH2", b"user=tobias@example.com\x01auth=Bearer AT\x01\x01")
    box.close()


def test_login_and_logout_commands(tmp_path, monkeypatch, capsys):
    mail, _ = connector(tmp_path)
    app = FakeApp(result={"access_token": "AT", "id_token_claims": {"preferred_username": "tobias@example.com"}})
    monkeypatch.setattr(mail, "account", lambda: Mail.account(mail, app))
    assert Mail.commands["login"].run(mail, SimpleNamespace(device_code=False)) == 0
    app.accounts.append({"username": "tobias@example.com"})
    assert Mail.commands["logout"].run(mail, SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "signed in as tobias@example.com" in out
    assert "tokens of 1 account removed" in out


class Answers:
    def __init__(self, *texts, yes=True):
        self.texts, self.yes_answer, self.asked = list(texts), yes, []

    def text(self, prompt, default=None, check=None):
        self.asked.append(prompt)
        answer = self.texts.pop(0)
        assert check is None or check(answer)
        return answer if answer or default is None else default

    def yes(self, prompt, default=True):
        self.asked.append(prompt)
        return self.yes_answer


def test_setup_asks_for_the_account(tmp_path):
    said = []
    table = Mail.setup(Answers("Tobias@Example.com", "cid-1", " t@work.example , "), said.append)
    assert "entra.microsoft.com" in said[0]
    path = tmp_path / "glean.toml"
    path.write_text(config.template({"mail": table}), encoding="utf-8")
    m = config.load(path).sources["mail"]
    assert (m.address, m.client_id) == ("tobias@example.com", "cid-1")
    assert m.my_addresses == ("tobias@example.com", "t@work.example")


def test_after_setup_signs_in_when_asked(tmp_path, monkeypatch):
    mail, _ = connector(tmp_path)
    app = FakeApp(result={"access_token": "AT", "id_token_claims": {"preferred_username": "tobias@example.com"}})
    monkeypatch.setattr(mail, "account", lambda: Mail.account(mail, app))
    mail.after_setup(Answers(yes=False))
    assert app.calls == []
    mail.after_setup(Answers(yes=True))
    assert len(app.calls) == 1
    eml, _ = connector(tmp_path, eml=True)
    eml.after_setup(Answers(yes=True))  # nothing to sign in to


def test_a_message_that_cannot_be_read_is_kept_as_failed(tmp_path, state, monkeypatch):
    root = eml_folder(tmp_path)
    (root / "poison.eml").write_bytes(b"Message-ID: <poison@x>\r\n\r\nPOISON\r\n")
    real = mail_message.parse

    def parse(raw):
        if b"POISON" in raw:
            raise IndexError("list index out of range")
        return real(raw)

    monkeypatch.setattr(mail_message, "parse", parse)
    mail, _ = connector(tmp_path, eml=True)
    report = mail.download(state)
    assert report.new == 4
    (poisoned,) = state.items("mail", "failed")
    assert poisoned.reason == "it couldn't be read: IndexError: list index out of range"
    assert (mail.archive / poisoned.path).read_bytes() == b"Message-ID: <poison@x>\r\n\r\nPOISON\r\n"
    assert poisoned.title == "(unreadable)"
    assert mail.download(state).new == 0  # not fetched again


def test_the_setup_guide_links_the_instructions_online():
    assert "https://github.com/tobiaswuerth/glean/blob/main/docs/microsoft-app.md" in GUIDE


def test_messages_the_server_did_not_send_are_fetched_next_time(tmp_path, state, monkeypatch):
    server = FakeImap({"Inbox": {"validity": 1, "messages": {1: samples.plain(), 2: samples.forwarded()}}})
    server.no_body = {2}  # left out of the response
    mail, said = connector(tmp_path)
    monkeypatch.setattr(mail, "mailbox", lambda: imap.Imap("h", 993, "u", lambda: "T", server))
    assert mail.download(state).new == 1
    assert "Inbox: 1 email didn't arrive; the next download asks again" in said
    server.no_body = set()
    assert mail.download(state).new == 1
    assert state.find("mail", "fwd-1@acme.example") is not None
    assert state.cursor("mail", "Inbox") == "1:2"


def test_eml_files_with_their_extension_in_capitals(tmp_path, state):
    root = tmp_path / "eml"
    root.mkdir()
    (root / "A.EML").write_bytes(samples.plain())
    (root / "notes.txt").write_bytes(b"not mail")
    box = EmlFolder(root)
    headers, _ = box.scan(box.folders()[0], None)
    assert [h.ref for h in headers] == ["A.EML"]


def test_an_eml_file_removed_while_downloading_is_passed_over(tmp_path):
    root = eml_folder(tmp_path)
    box = EmlFolder(root)
    inbox = next(f for f in box.folders() if f.name == "Inbox")
    headers, _ = box.scan(inbox, None)
    (root / "a.eml").unlink()
    assert [ref for ref, _ in box.fetch(inbox, headers)] == ["b.eml"]


JUNK = [("X-MS-Exchange-Organization-SCL", "6"), ("X-Microsoft-Antispam-Mailbox-Delivery", "dest:J;RF:JunkEmail;")]


def test_what_filters_say_goes_to_the_agent_as_evidence(tmp_path, state):
    # A support desk's reply Microsoft rated spam was a person's: filters err, so the agent reads and decides.
    root = tmp_path / "eml"
    (root / "Sent").mkdir(parents=True)
    (root / "Sent" / "1.eml").write_bytes(samples.plain(message_id="<ask@x>", sender="tobias@example.com",
                                                        to="Shop <hello@bags.example>"))  # fmt: skip
    (root / "2.eml").write_bytes(samples.plain(message_id="<answer@x>", sender="Sina <hello@bags.example>",
                                               subject="AW: Ihre Anfrage", headers=JUNK))  # fmt: skip
    (root / "3.eml").write_bytes(samples.plain(message_id="<spam@x>", sender="x@spam.example", headers=JUNK))
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    assert {item.status for item in state.items("mail")} == {"new"}
    mail.upgrade(state)  # as before every run: it learns whom the owner wrote to
    spam = mail.document(state.find("mail", "spam@x"))
    assert spam.signals == ("Microsoft's filter rated it spam (SCL 6)", "Microsoft's filter sent it to junk")
    answer = mail.document(state.find("mail", "answer@x"))
    assert answer.signals[-1] == "the owner has written to its sender before"
    assert mail.document(state.find("mail", "ask@x")).signals == ()  # what the owner wrote is theirs


def test_what_was_skipped_before_its_sender_became_a_contact_is_let_in(tmp_path, state):
    root = tmp_path / "eml"
    root.mkdir()
    (root / "2.eml").write_bytes(samples.plain(message_id="<answer@x>", sender="hello@bags.example"))
    mail, said = connector(tmp_path, 'skip_senders = ["hello@*"]\n', eml=True)
    mail.download(state)
    skipped = state.find("mail", "answer@x")
    assert (skipped.status, skipped.reason) == ("skipped", "sender: hello@*")
    (root / "Sent").mkdir()
    (root / "Sent" / "1.eml").write_bytes(samples.plain(message_id="<ask@x>", sender="tobias@example.com",
                                                        to="hello@bags.example"))  # fmt: skip
    mail.download(state)
    answer = state.find("mail", "answer@x")
    assert (answer.status, answer.reason) == ("new", None)
    assert (mail.archive / answer.document).is_file()
    assert "1 skipped email from people you wrote to: waiting to be imported" in said


def test_contacts_are_gathered_from_outbound_mail_downloaded_before(tmp_path, state):
    root = tmp_path / "eml"
    (root / "Sent").mkdir(parents=True)
    for n, to in enumerate(["Hans <hans@acme.example>", "lea@acme.example"]):
        sent = samples.plain(
            message_id=f"<s{n}@x>", sender="tobias@example.com", to=to, headers=[("Cc", "tobias@example.com")]
        )
        (root / "Sent" / f"{n}.eml").write_bytes(sent)
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    state.db.execute("DELETE FROM mail.contacts")  # as glean 0.1 left them: none
    state.db.commit()
    (mail.archive / state.find("mail", "s1@x").path).unlink()  # an original gone names no contacts
    mail.download(state)
    assert state.contacts("mail") == {"hans@acme.example"}  # the owner's own address isn't a contact


def test_every_downloaded_email_has_its_document(tmp_path, state):
    eml_folder(tmp_path)
    (tmp_path / "eml" / "spam.eml").write_bytes(samples.plain(message_id="<spam@x>", sender="x@y.example",
                                                              headers=JUNK))  # fmt: skip
    (tmp_path / "eml" / "bot.eml").write_bytes(samples.plain(message_id="<bot@x>", sender="bot@y.example"))
    mail, _ = connector(tmp_path, 'skip_senders = ["bot@*"]\n', eml=True)
    mail.download(state)
    for item in state.items("mail"):
        assert (mail.archive / item.document).read_text(encoding="utf-8").startswith('---\ntype: "email"')
    assert state.find("mail", "bot@x").status == "skipped"  # skipped by the owner's list, yet searchable


def test_a_document_lists_every_folder_its_email_is_found_in(tmp_path, state, monkeypatch):
    server = FakeImap(
        {
            "Inbox": {"validity": 1, "messages": {1: samples.plain()}},
            "Archive": {"validity": 1, "messages": {4: samples.plain()}},
        }
    )
    mail, _ = connector(tmp_path)
    monkeypatch.setattr(mail, "mailbox", lambda: imap.Imap("h", 993, "u", lambda: "T", server))
    mail.download(state)
    hans = state.find("mail", "caf1234@mail.acme.example")
    assert 'folders: ["Archive", "Inbox"]' in (mail.archive / hans.document).read_text(encoding="utf-8")


def test_contacts_known_already_are_not_gathered_again(tmp_path, state, monkeypatch):
    root = tmp_path / "eml"
    (root / "Sent").mkdir(parents=True)
    (root / "Sent" / "1.eml").write_bytes(samples.plain(message_id="<s@x>", sender="tobias@example.com",
                                                        to="hans@acme.example"))  # fmt: skip
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    monkeypatch.setattr(mail_message, "parse", lambda raw: pytest.fail("no original is read again"))
    mail.download(state)
    assert state.contacts("mail") == {"hans@acme.example"}


def test_what_an_email_shares_with_others_it_must_not_run_beside(tmp_path, state):
    eml_folder(tmp_path)
    (tmp_path / "eml" / "c.eml").write_bytes(samples.with_attachments())  # a reply in Hans's thread, Cc Lea
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    offer = mail.document(state.find("mail", "caf1234@mail.acme.example")).related
    reply = mail.document(state.find("mail", "att-1@acme.example")).related
    assert "address:hans.mueller@acme.example" in offer & reply
    assert "domain:acme.example" in offer
    assert "thread:caf1234@mail.acme.example" in offer  # its own id: it starts the thread
    assert "thread:first@x.example" in reply  # the first of its references
    assert not any("tobias@example.com" in key for key in offer | reply)  # the owner is in everything
    mine = mail.document(state.find("mail", "mine@x")).related  # from the owner to the owner
    assert mine == {"thread:mine@x"}


def test_free_mail_domains_relate_no_one():
    m = mail_message.parse(samples.plain(sender="Anna <anna@gmail.com>", to="tobias@example.com"))
    keys = Mail.related(m, ("tobias@example.com",))
    assert "address:anna@gmail.com" in keys
    assert not any(key.startswith("domain:") for key in keys)


def test_mail_downloaded_by_an_older_glean_is_brought_in_line(tmp_path, state):
    root = eml_folder(tmp_path)
    (root / "bulk.eml").write_bytes(samples.plain(message_id="<alert@x>", sender="Alerts <alerts@ex.example>"))
    mail, said = connector(tmp_path, 'skip_senders = ["alerts@*"]\n', eml=True)
    mail.download(state)
    # as older gleans left them: no documents, their own rules' skips, and the sender the owner lists since let in
    state.db.execute("UPDATE mail.items SET document = NULL")
    state.db.execute(
        "UPDATE mail.items SET status = 'skipped', reason = 'bulk: List-Unsubscribe' WHERE key LIKE 'html%'"
    )
    state.db.execute("UPDATE mail.items SET status = 'new', reason = NULL WHERE key = 'alert@x'")
    state.db.execute("DELETE FROM mail.marks")
    ran = state.find("mail", "caf1234@mail.acme.example")
    state.finish_run(state.start_run(ran), ran, status=None, outcome="interrupted", cost_usd=0, turns=0,
                     duration_ms=0)  # fmt: skip
    state.db.commit()
    mail.upgrade(state)
    alert = state.find("mail", "alert@x")
    assert (alert.status, alert.reason) == ("skipped", "sender: alerts@*")
    assert state.find("mail", "html-1@shop.example").status == "new"  # the agent decides now
    assert all(item.document for item in state.items("mail"))
    assert state.find("mail", "caf1234@mail.acme.example").status == "new"  # one an agent ran on keeps its status
    assert any("brought 4 emails downloaded before in line" in s for s in said)
    said.clear()
    mail.upgrade(state)  # once per version
    assert said == []


def test_the_upgrade_passes_over_what_it_cannot_use(tmp_path, state):
    root = eml_folder(tmp_path)
    (root / "poison.eml").write_bytes(b"Message-ID: <poison@x>\r\n\r\nPOISON\r\n")
    mail, _ = connector(tmp_path, eml=True)
    mail.download(state)
    state.db.execute("UPDATE mail.items SET status = 'failed' WHERE key = 'poison@x'")
    state.db.execute("UPDATE mail.items SET document = NULL WHERE key = 'html-1@shop.example'")
    state.db.execute("DELETE FROM mail.marks")
    state.db.commit()
    gone = state.find("mail", "html-1@shop.example")
    (mail.archive / gone.path).unlink()
    mail.upgrade(state)  # a failed item is left; an original gone is left; documents there already stay
    assert state.find("mail", "poison@x").status == "failed"
    assert state.find("mail", "html-1@shop.example").document is None
    assert state.mark("mail", "version") == VERSION


def test_a_document_that_cannot_be_written_stops_neither_download_nor_upgrade(tmp_path, state, monkeypatch):
    eml_folder(tmp_path)
    (tmp_path / "eml" / "Sent" / "e.eml").write_bytes(samples.html_only())  # found again: no document to refresh
    real = markdown_module.write

    def write(parsed, item, folder, save):
        if parsed.subject == "Newsletter":
            raise ValueError("a character this file system refuses")
        return real(parsed, item, folder, save)

    monkeypatch.setattr(markdown_module, "write", write)
    mail, said = connector(tmp_path, eml=True)
    assert mail.download(state).new == 3
    assert state.find("mail", "html-1@shop.example").document is None
    assert any(s.startswith("warning: the document of ") and "a character this file system refuses" in s for s in said)
    state.db.execute("DELETE FROM mail.marks")
    state.db.commit()
    mail.upgrade(state)
    assert state.mark("mail", "version") == VERSION
