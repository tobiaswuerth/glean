"""Parsing an email, with Python's `email` package and its modern policy: headers decoded (RFC 2047), addresses with
their display names, the date with its offset, the threading headers, the body as text, and the attachments. Mail as
it comes is often broken, so nothing here fails on a bad header, an unknown charset or a missing Message-ID."""

import email
import email.policy
import email.utils
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property

from . import html

MESSAGE_ID = re.compile(r"<([^<>\s]+)>")
RAW_BYTES = re.compile("[\udc80-\udcff]")  # how the email package keeps an undecodable byte of a header
FILTER_HEADERS = (  # what says a mail is bulk, automated or spam: list headers, and the verdicts of Outlook.com
    "list-unsubscribe",
    "list-id",
    "precedence",
    "auto-submitted",
    "x-ms-exchange-organization-scl",
    "x-forefront-antispam-report",
    "x-microsoft-antispam",
    "x-microsoft-antispam-mailbox-delivery",
)
STAND_INS = (  # how a notice a server sends over IMAP in place of a message it can't convert begins
    "Retrieval using the IMAP4 protocol failed for the following message",  # Exchange, Outlook.com
)


@dataclass(frozen=True)
class Address:
    name: str
    email: str  # in lower case

    def __str__(self):
        return f"{self.name} <{self.email}>" if self.name else self.email


@dataclass(frozen=True)
class Attachment:
    name: str
    content_type: str
    data: bytes
    inline: bool  # shown in the body, such as a logo in a signature

    @property
    def size(self):
        return len(self.data)

    @cached_property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class Message:
    message_id: str  # normalized; made from the raw bytes' digest if the email has none
    has_message_id: bool
    date: datetime | None  # with its offset; None if missing or unreadable
    subject: str
    from_: tuple[Address, ...]
    sender: Address | None  # who sent it on behalf of From
    reply_to: tuple[Address, ...]
    to: tuple[Address, ...]
    cc: tuple[Address, ...]
    bcc: tuple[Address, ...]
    in_reply_to: str | None
    references: tuple[str, ...]  # oldest first
    headers: dict[str, str]  # FILTER_HEADERS, by lower-case name
    text: str
    attachments: tuple[Attachment, ...]
    attached: tuple["Message", ...]  # messages attached whole, as forwarding as an attachment does


def normalize_id(value):
    """A Message-ID as glean and orbit key it: the first <…> in the value, without the brackets, in lower case."""
    if not value:
        return None
    found = MESSAGE_ID.search(value)
    text = (found.group(1) if found else value).strip().strip("<>").strip()
    return text.lower() or None


def parse(raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return _message(msg, hashlib.sha256(raw).hexdigest())


def stand_in(parsed):
    """Why this isn't the message the mailbox holds but a notice its server sent in its place, or None."""
    if not parsed.has_message_id and parsed.subject.startswith(STAND_INS):
        return "the mail server sent a notice in its place: it couldn't convert the message for IMAP"
    return None


def _message(msg, digest):
    found = normalize_id(_header(msg, "message-id"))
    body, attachments, attached = [], [], []
    _walk(msg, body, attachments, attached)
    senders = _addresses(msg, "sender")
    return Message(
        message_id=found or f"{digest[:32]}@glean.invalid",
        has_message_id=found is not None,
        date=_date(msg),
        subject=" ".join(_header(msg, "subject").split()),
        from_=_addresses(msg, "from"),
        sender=senders[0] if senders else None,
        reply_to=_addresses(msg, "reply-to"),
        to=_addresses(msg, "to"),
        cc=_addresses(msg, "cc"),
        bcc=_addresses(msg, "bcc"),
        in_reply_to=normalize_id(_header(msg, "in-reply-to")),
        references=tuple(m.lower() for m in MESSAGE_ID.findall(_header(msg, "references"))),
        headers={h: v for h in FILTER_HEADERS if (v := _header(msg, h))},
        text="\n\n".join(t for t in body if t),
        attachments=tuple(attachments),
        attached=tuple(attached),
    )


def _unescape(text):
    """Text in which the email package kept raw 8-bit bytes as lone surrogates, as it does for a header written in
    UTF-8 or Latin-1 without the encoding RFC 2047 asks for: those bytes read as UTF-8, else as Windows-1252."""
    if not RAW_BYTES.search(text):
        return text
    raw = text.encode("utf-8", "surrogateescape")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _header(msg, name):
    """A header's decoded text; "" if it is missing. A header too broken to decode is taken as it is."""
    try:
        value = msg.get(name)
    except Exception:  # the email package fails on some malformed headers
        value = next((v for k, v in msg.raw_items() if k.lower() == name), None)
    return _unescape(str(value).strip()) if value is not None else ""


def _addresses(msg, name):
    try:
        header = msg.get(name)
        found = [(a.display_name, a.addr_spec) for a in header.addresses] if header is not None else []
    except Exception:
        found = email.utils.getaddresses([_header(msg, name)])
    return tuple(Address(" ".join(_unescape(n).split()), _unescape(a).strip().lower()) for n, a in found if a.strip())


def _date(msg):
    try:
        when = msg["date"].datetime if msg["date"] is not None else None
    except Exception:
        when = None
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)  # "-0000": the time is UTC, the zone unknown


def _is_attachment(part):
    """Whether a leaf part is an attachment rather than body text: a file, a calendar invitation, or an image shown
    in the body."""
    disposition = part.get_content_disposition()
    if disposition == "attachment":
        return True
    if part.get_content_type() in ("text/plain", "text/html"):
        return False
    return (
        part.get_filename() is not None
        or part.get_content_type() == "text/calendar"
        or (part.get_content_maintype() != "text")
    )


def _walk(part, body, attachments, attached):
    """Collect the body texts, the attachments and the attached messages of a part, in order. Of the versions in a
    multipart/alternative, the body takes the first that has text: plain before HTML."""
    if part.get_content_type() == "message/rfc822":
        inner = part.get_content()
        attached.append(_message(inner, hashlib.sha256(inner.as_bytes()).hexdigest()))
        return
    if part.is_multipart():
        children = list(part.iter_parts())
        if part.get_content_subtype() != "alternative":
            for child in children:
                _walk(child, body, attachments, attached)
            return
        chosen = None
        for wanted in ("text/plain", "text/html", None):
            for child in children:
                if chosen is None and (wanted is None or child.get_content_type() == wanted):
                    texts = []
                    _walk(child, texts, [], [])
                    if any(t.strip() for t in texts):
                        chosen = child
        for child in children:  # attachments and inline images of every version, the body of the chosen one
            _walk(child, body if child is chosen else [], attachments, attached)
        return
    if _is_attachment(part):
        inline = part.get_content_maintype() == "image" and (
            part.get_content_disposition() != "attachment" or part.get("content-id") is not None
        )
        name = _unescape(part.get_filename() or f"attachment{_extension(part)}")
        attachments.append(Attachment(name, part.get_content_type(), _payload(part), inline))
    elif part.get_content_type() == "text/html":
        body.append(html.to_text(_text(part)))
    elif part.get_content_type() == "text/plain":
        body.append(_clean(_text(part)))


def _payload(part):
    return part.get_payload(decode=True) or b""


def _extension(part):
    return {"application/pdf": ".pdf", "text/calendar": ".ics"}.get(part.get_content_type(), "")


def _text(part):
    """A text part's content as text, in its charset; an unknown or wrong charset is read as UTF-8, else as
    Windows-1252, which decodes any byte."""
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        data = _payload(part)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("cp1252", "replace")


def _clean(text):
    """Text with Unix line ends and no blank lines around it; inside, it stays as written."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()
