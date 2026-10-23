"""An email as a Markdown document: front matter sift and the agent read, the text, attachments saved for sift."""

import datetime
import hashlib
import json

from glean import archive
from glean.connectors.mail import markdown, message, options
from glean.state import Item

from . import samples


def item(raw, **kwargs):
    fields = {
        "source": "mail",
        "key": "k",
        "date": "2026-10-15T14:05:00+02:00",
        "title": "t",
        "author": "a",
        "container": "Inbox",
        "direction": "inbound",
        "path": "x/message.eml",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "containers": ("Archive", "Inbox"),
    }
    return Item(**(fields | kwargs))


def front(text):
    block = text.split("---\n")[1]
    return {k: json.loads(v) for k, v in (line.split(": ", 1) for line in block.splitlines())}


def test_the_document_and_its_attachments(tmp_path):
    raw = samples.with_attachments()
    m = message.parse(raw)
    doc = markdown.write(m, item(raw), tmp_path, options.SAVE_ATTACHMENTS)
    local = m.date.astimezone()
    assert doc.markdown == tmp_path / f"{local:%Y-%m-%d %H%M} Unterlagen.md"
    assert doc.text == doc.markdown.read_text(encoding="utf-8")
    assert doc.original == tmp_path / "message.eml"
    assert doc.sha256 == hashlib.sha256(raw).hexdigest()
    assert [p.name for p in doc.files] == ["Offerte 2027.pdf", "Offerte 2027 (2).pdf"]
    assert (tmp_path / "Offerte 2027.pdf").read_bytes() == samples.PDF
    assert not (tmp_path / "Foto.png").exists()  # sift can't read images
    assert not (tmp_path / "data.csv").exists()
    f = front(doc.text)
    assert f["type"] == "email"
    assert f["message_id"] == "att-1@acme.example"
    assert f["date"] == "2026-10-15T14:05:00+02:00"
    assert (f["folder"], f["folders"], f["direction"]) == ("Inbox", ["Archive", "Inbox"], "inbound")
    assert f["from"] == "hans.mueller@acme.example"
    assert f["to"] == ["tobias@example.com"]
    assert f["cc"] == ["Lea Frei <lea.frei@acme.example>", "info@acme.example"]
    assert f["sender"] is None
    assert f["in_reply_to"] == "caf1234@mail.acme.example"
    assert f["references"] == ["first@x.example", "caf1234@mail.acme.example"]
    assert f["attachments"] == ["Offerte 2027.pdf", "Foto.png", "data.csv", "Offerte 2027.pdf"]  # not the logo
    assert f["original"] == "message.eml"
    assert f["sha256"] == doc.sha256
    body = doc.text.split("---\n", 2)[2]
    assert body.startswith("\nPlain body\n\n## Attachments\n\n")
    assert "- Foto.png (image/png, 40 bytes, sha256 " in body
    assert "): not saved" in body
    assert "saved as [Offerte 2027 (2).pdf](<Offerte 2027 (2).pdf>)" in body
    assert "logo.png" not in body


def test_writing_again_gives_the_same_files(tmp_path):
    raw = samples.with_attachments()
    m = message.parse(raw)
    first = markdown.write(m, item(raw), tmp_path, options.SAVE_ATTACHMENTS)
    second = markdown.write(m, item(raw), tmp_path, options.SAVE_ATTACHMENTS)
    assert first == second
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [first.markdown.name, "Offerte 2027.pdf", "Offerte 2027 (2).pdf"]
    )


def test_an_older_document_name_is_replaced(tmp_path):
    (tmp_path / "2020-01-01 0000 old.md").write_text("old", encoding="utf-8")
    raw = samples.plain()
    doc = markdown.write(message.parse(raw), item(raw), tmp_path, ())
    assert [p.name for p in tmp_path.glob("*.md")] == [doc.markdown.name]


def test_forwarded_messages_become_sections(tmp_path):
    raw = samples.forwarded()
    doc = markdown.write(message.parse(raw), item(raw, containers=()), tmp_path, ())
    assert front(doc.text)["folders"] == ["Inbox"]
    assert "## Attached message: Termin\n\nFrom: Lea Frei <lea.frei@acme.example>\nDate: 2026-10-12T10:00:00+02:00" in (
        doc.text
    )
    assert "Subject: Termin\n\nPasst Dienstag?" in doc.text


def test_an_email_without_date_subject_or_text(tmp_path):
    raw = b"From: a@example.com\r\nCc: b@example.com\r\n\r\n"
    doc = markdown.write(message.parse(raw), item(raw), tmp_path, ())
    assert doc.markdown.name == "undated (no subject).md"
    assert front(doc.text)["date"] is None
    assert doc.text.endswith("---\n\n(no text)\n")


def test_nested_attached_messages_and_cc(tmp_path):
    inner = samples.forwarded()
    raw = (
        b"From: a@example.com\r\nSubject: Outer\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="o"\r\n\r\n--o\r\nContent-Type: text/plain\r\n\r\nOuter\r\n'
        b"--o\r\nContent-Type: message/rfc822\r\n\r\n"
        + inner.replace(b"To: tobias", b"Cc: c@example.com\r\nTo: tobias")
        + b"\r\n--o--\r\n"
    )  # fmt: skip
    doc = markdown.write(message.parse(raw), item(raw), tmp_path, ())
    assert "## Attached message: WG: Termin" in doc.text
    assert "Cc: c@example.com" in doc.text
    assert "## Attached message: Termin" in doc.text


def test_file_names():
    when = datetime.datetime(2026, 10, 14, 9, 32)
    assert markdown.file_name(when, "Re: AW: Offerte?") == "2026-10-14 0932 Re AW Offerte.md"
    assert len(markdown.file_name(when, "x " * 200)) <= archive.NAME_LIMIT + 3


def test_sizes():
    assert markdown._size(10) == "10 bytes"
    assert markdown._size(2048) == "2.0 KB"
    assert markdown._size(5 << 20) == "5.0 MB"
    assert markdown._size(5 << 30) == "5120.0 MB"
