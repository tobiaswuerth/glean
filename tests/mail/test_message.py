"""Parsing an email: headers decoded, addresses with names, dates with offsets, the body as text, attachments."""

import datetime
import hashlib

import pytest

from glean.connectors.mail import message

from . import samples


def test_a_plain_email():
    m = message.parse(samples.plain())
    assert m.message_id == "caf1234@mail.acme.example"
    assert m.has_message_id
    assert m.subject == "Offerte Wartungsvertrag 2027"
    assert m.date == datetime.datetime(2026, 10, 14, 9, 32, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    assert [str(a) for a in m.from_] == ["Hans Müller <hans.mueller@acme.example>"]
    assert m.to[0].name == "Tobias Würth"
    assert m.to[0].email == "tobias@example.com"
    assert m.cc == m.bcc == m.reply_to == ()
    assert m.sender is None
    assert m.in_reply_to is None
    assert m.references == ()
    assert m.text.startswith("Grüezi Herr Würth\n\nAnbei die Offerte.")
    assert "\r" not in m.text
    assert m.attachments == m.attached == ()


def test_encoded_headers_and_a_latin1_body():
    m = message.parse(samples.ENCODED)
    assert m.message_id == "enc-1@example.com"  # lower case, without brackets
    assert m.subject == "Grüsse aus Zürich"
    assert str(m.from_[0]) == "Jürg Müller <juerg@example.ch>"
    assert m.to == ()  # an empty group
    assert str(m.sender) == "Assistentin <assist@example.ch>"
    assert str(m.reply_to[0]) == "Team <team@example.ch>"
    assert m.date.tzinfo is not None  # -0000: UTC
    assert m.date.utcoffset() == datetime.timedelta(0)
    assert m.text == "Grüezi mitenand"


def test_threading_headers():
    m = message.parse(samples.with_attachments())
    assert m.in_reply_to == "caf1234@mail.acme.example"
    assert m.references == ("first@x.example", "caf1234@mail.acme.example")
    assert [a.email for a in m.cc] == ["lea.frei@acme.example", "info@acme.example"]


def test_the_plain_part_is_preferred_and_attachments_are_listed():
    m = message.parse(samples.with_attachments())
    assert m.text == "Plain body"
    names = [(a.name, a.content_type, a.inline) for a in m.attachments]
    assert names == [
        ("logo.png", "image/png", True),
        ("Offerte 2027.pdf", "application/pdf", False),
        ("Foto.png", "image/png", False),
        ("data.csv", "text/csv", False),
        ("Offerte 2027.pdf", "application/pdf", False),
    ]
    pdf = m.attachments[1]
    assert pdf.data == samples.PDF
    assert pdf.size == len(samples.PDF)
    assert pdf.sha256 == hashlib.sha256(samples.PDF).hexdigest()


def test_html_only_mail_becomes_readable_text():
    m = message.parse(samples.html_only())
    assert "Angebote" in m.text
    assert "Preis" in m.text
    assert "CHF 10" in m.text
    assert "Eins" in m.text
    assert "https://shop.example/x" in m.text
    assert "color: red" not in m.text
    assert "alert" not in m.text
    assert m.headers["list-unsubscribe"] == "<mailto:unsubscribe@shop.example>"


def test_an_empty_plain_part_gives_way_to_html():
    assert message.parse(samples.EMPTY_PLAIN).text == "Only in HTML"


def test_text_around_an_inline_attachment_is_kept():
    m = message.parse(samples.APPLE_SPLIT)
    assert m.text == "Before the file.\n\nAfter the file."
    assert [a.name for a in m.attachments] == ["a.pdf"]


def test_a_forwarded_message_is_parsed_too():
    m = message.parse(samples.forwarded())
    assert m.text == "Siehe unten."
    (inner,) = m.attached
    assert inner.subject == "Termin"
    assert str(inner.from_[0]) == "Lea Frei <lea.frei@acme.example>"
    assert inner.text == "Passt Dienstag?"


def test_a_broken_email_still_parses():
    m = message.parse(samples.BROKEN)
    assert not m.has_message_id
    assert m.message_id == hashlib.sha256(samples.BROKEN).hexdigest()[:32] + "@glean.invalid"
    assert m.date is None
    assert m.subject == "Kaputt"
    assert m.text == "Text ä"  # an unknown charset: UTF-8, else Windows-1252
    assert [a.email for a in m.from_] == ["nobody"]


@pytest.mark.parametrize(
    "value, normalized",
    [
        ("<ABC@Example.com>", "abc@example.com"),
        ("  <a@b>  <c@d>", "a@b"),
        ("abc@example.com", "abc@example.com"),
        ("", None),
        (None, None),
        ("<>", None),
    ],
)
def test_message_ids_are_normalized(value, normalized):
    assert message.normalize_id(value) == normalized


def test_line_ends_become_unix_and_blank_lines_inside_stay():
    raw = samples.plain(body="Line one\r\nLine two\r\n\r\n\r\n\r\nLine three\r\n")
    assert message.parse(raw).text == "Line one\nLine two\n\n\n\nLine three"


class Broken:
    """A message whose headers the email package can't read."""

    def get(self, name):
        raise ValueError("bad header")

    def __getitem__(self, name):
        raise ValueError("bad header")

    def raw_items(self):
        return [("To", "Hans <H@X.example>"), ("Subject", "raw")]


def test_headers_too_broken_to_parse_are_taken_as_written():
    assert message._header(Broken(), "subject") == "raw"
    assert message._header(Broken(), "cc") == ""
    assert message._addresses(Broken(), "to") == (message.Address("Hans", "h@x.example"),)
    assert message._date(Broken()) is None


def test_other_text_parts_are_left_out_and_unnamed_files_named():
    raw = (
        b'From: a@example.com\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="m"\r\n\r\n'
        b"--m\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
        b"--m\r\nContent-Type: text/enriched\r\n\r\n<bold>x</bold>\r\n"
        b"--m\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\n"
        b"--m\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment\r\n\r\nxx\r\n"
        b"--m--\r\n"
    )
    m = message.parse(raw)
    assert m.text == "Body"
    assert [a.name for a in m.attachments] == ["attachment.ics", "attachment"]


RAW_8BIT = (
    b"Message-ID: <raw@x>\r\n"
    b'From: "J\xc3\xbcrg M\xc3\xbcller" <juerg@example.ch>\r\n'  # UTF-8, not encoded as RFC 2047 asks
    b'To: "Tobias W\xfcrth" <tobias@example.com>\r\n'  # Latin-1, the same
    b"Subject: Gr\xc3\xbcsse\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Hallo\r\n"
)


def test_headers_written_in_raw_8_bit_are_read_as_text():
    m = message.parse(RAW_8BIT)
    assert m.subject == "Grüsse"
    assert str(m.from_[0]) == "Jürg Müller <juerg@example.ch>"
    assert m.to[0].name == "Tobias Würth"
    "".join([m.subject, *map(str, m.from_), *map(str, m.to)]).encode("utf-8")  # no lone surrogates left
