"""The [mail] table: defaults, validation, and the table the setup wizard writes."""

import pytest

from glean import config
from glean.connectors.mail import options
from glean.errors import GleanError

MINIMAL = """
[mail]
address = "Tobias@Example.com"
client_id = "11111111-2222-3333-4444-555555555555"
"""


def load(tmp_path, text):
    path = tmp_path / "glean.toml"
    path.write_text(text, encoding="utf-8")
    return config.load(path).sources["mail"]


def test_defaults(tmp_path):
    m = load(tmp_path, MINIMAL)
    assert m.address == "tobias@example.com"
    assert m.my_addresses == ("tobias@example.com",)
    assert (m.host, m.port) == ("outlook.office365.com", 993)
    assert m.authority == options.CONSUMERS
    assert m.redirect_uri == "http://localhost"
    assert m.archive == tmp_path / "mails"
    assert m.collection == "mails"
    assert m.folders == ()
    assert m.exclude_folders == options.EXCLUDE_FOLDERS
    assert m.save_attachments == options.SAVE_ATTACHMENTS
    assert m.skip_senders == ()  # glean skips none by itself: the agent decides
    assert m.eml_folder is None


def test_lists_add_to_glean_s_own(tmp_path):
    m = load(tmp_path, MINIMAL + 'exclude_folders = ["Spam", "junk"]\nskip_senders = ["Bot@*", "alerts@*"]\n')
    assert m.exclude_folders == (*options.EXCLUDE_FOLDERS, "spam")
    assert m.skip_senders == ("bot@*", "alerts@*")


def test_every_setting(tmp_path):
    text = MINIMAL + (
        'my_addresses = ["T.W@Work.example", "tobias@example.com"]\n'
        'authority = "https://login.microsoftonline.com/common"\nredirect_uri = "http://localhost:8400"\n'
        'host = "imap.example.com"\nport = 1993\nfolders = ["Inbox"]\nexclude_folders = ["Spam"]\n'
        'archive = "D:/archive"\ncollection = "post"\nsave_attachments = ["PDF", ".DOCX"]\n'
        'skip_senders = ["Bot@*"]\neml_folder = "saved"\n'
    )
    m = load(tmp_path, text)
    assert m.my_addresses == ("tobias@example.com", "t.w@work.example")
    assert m.authority.endswith("/common")
    assert m.redirect_uri == "http://localhost:8400"
    assert (m.host, m.port, m.folders) == ("imap.example.com", 1993, ("Inbox",))
    assert m.exclude_folders[-1] == "spam"
    assert m.archive == (tmp_path / "D:/archive").resolve()
    assert m.collection == "post"
    assert m.save_attachments == (".pdf", ".docx")
    assert m.skip_senders == ("bot@*",)
    assert m.eml_folder == tmp_path / "saved"


def test_an_eml_folder_needs_no_client_id(tmp_path):
    m = load(tmp_path, '[mail]\naddress = "a@b.example"\neml_folder = "saved"\n')
    assert m.client_id is None


@pytest.mark.parametrize(
    "text, message",
    [
        ("[mail]\naddress = 'a@b.example'\n", "mail.client_id is missing"),
        ("[mail]\nclient_id = 'x'\n", "mail.address is missing"),
        ("[mail]\naddress = 'not an address'\nclient_id = 'x'\n", "mail.address must be an email address"),
        (MINIMAL + "colour = 'red'\n", "unknown setting mail.colour"),
        (MINIMAL + "port = 'x'\n", "mail.port must be a port number"),
        (MINIMAL + "port = 70000\n", "mail.port must be a port number"),
        (MINIMAL + "folders = 'Inbox'\n", "mail.folders must be a list of text"),
        (MINIMAL + "host = ''\n", "mail.host must be text"),
        (MINIMAL + "skip_bulk = true\n", "mail.skip_bulk is gone: what the headers say is evidence for the agent"),
        (MINIMAL + "skip_spam = false\n", "mail.skip_spam is gone"),
        (MINIMAL + "eml_folder = 3\n", "mail.eml_folder must be text"),
    ],
)
def test_bad_settings_are_named(tmp_path, text, message):
    with pytest.raises(GleanError, match=message):
        load(tmp_path, text)


def test_the_template_loads_as_written(tmp_path):
    table = options.template("me@example.com", 'id "quoted"', ["me@work.example"])
    path = tmp_path / "glean.toml"
    path.write_text(config.template({"mail": table}), encoding="utf-8")
    m = config.load(path).sources["mail"]
    assert m.client_id == 'id "quoted"'
    assert m.my_addresses == ("me@example.com", "me@work.example")
    assert m.exclude_folders == options.EXCLUDE_FOLDERS
    assert m.save_attachments == options.SAVE_ATTACHMENTS
    assert m.skip_senders == ()
    assert m.eml_folder is None
    assert "# skip_senders" in table  # the choices glean makes are shown, not written out
