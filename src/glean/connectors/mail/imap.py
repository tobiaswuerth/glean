"""Reading a mailbox over IMAP, read-only: folders are opened with EXAMINE and messages fetched with BODY.PEEK[], so
nothing is marked read, moved, flagged or deleted. Sign-in is OAuth2 (XOAUTH2). The commands sent are AUTHENTICATE,
LIST, EXAMINE, UID SEARCH, UID FETCH and LOGOUT, nothing else."""

import base64
import imaplib
import re
from contextlib import suppress
from datetime import datetime

from ...errors import GleanError
from .mailbox import Folder, Header, Mailbox

LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?P<delimiter>"(?:[^"\\]|\\.)*"|NIL)\s*(?P<name>.*)$', re.IGNORECASE)
UID = re.compile(rb"\bUID (\d+)", re.IGNORECASE)
SIZE = re.compile(rb"\bRFC822\.SIZE (\d+)", re.IGNORECASE)
INTERNALDATE = re.compile(rb'\bINTERNALDATE "([^"]+)"', re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
HEADER_BATCH = 500  # messages per FETCH of headers
BODY_BATCH_BYTES = 25 << 20  # bytes per FETCH of whole messages, by their RFC822.SIZE
BODY_BATCH_COUNT = 200
TIMEOUT = 120  # seconds a single IMAP response may take


def xoauth2(user, token):
    """The SASL XOAUTH2 initial response (imaplib encodes it in base64)."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01".encode()


def decode_folder_name(name):
    """IMAP's modified UTF-7 (RFC 3501): "Entw&APw-rfe" is "Entwürfe". A name that isn't valid stays as it is."""

    def decode(match):
        chunk = match.group(1)
        if not chunk:
            return "&"
        data = chunk.replace(",", "/")
        return base64.b64decode(data + "=" * (-len(data) % 4), validate=True).decode("utf-16-be")

    try:
        return re.sub(r"&([^-]*)-", decode, name)
    except ValueError:  # not base64, or not UTF-16
        return name


def quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(raw):
    if raw.startswith(b'"') and raw.endswith(b'"'):
        return re.sub(rb"\\(.)", rb"\1", raw[1:-1])
    return raw


def internal_date(text):
    """An INTERNALDATE, "14-Oct-2026 09:32:00 +0200", as a datetime; None if it can't be read."""
    m = re.fullmatch(r"\s*(\d{1,2})-(\w{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-]\d{4})", text)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    day, month, year, hh, mm, ss, zone = m.groups()
    stamp = f"{year}-{MONTHS[month.lower()]:02d}-{int(day):02d}T{hh}:{mm}:{ss}{zone[:3]}:{zone[3:]}"
    return datetime.fromisoformat(stamp)


def _responses(data):
    """The (metadata, literal) of each message in a FETCH response. imaplib gives a literal as a tuple (the text
    before it, the literal), and the rest of that message's response, such as " UID 7)", as the next item."""
    found = []
    for entry in data:
        if isinstance(entry, tuple):
            found.append([entry[0], entry[1]])
        elif isinstance(entry, bytes) and found:
            found[-1][0] += b" " + entry
    return found


class Imap(Mailbox):
    def __init__(self, host, port, user, token, connect=None):
        self.user, self.token = user, token  # token: a function that returns a current access token
        self.selected = None
        try:
            self.conn = (connect or imaplib.IMAP4_SSL)(host, port, timeout=TIMEOUT)
        except OSError as e:
            raise GleanError(f"can't reach {host}:{port}: {e}") from None
        try:
            self.conn.authenticate("XOAUTH2", lambda _: xoauth2(self.user, self.token()))
        except (imaplib.IMAP4.abort, OSError) as e:
            self.close()
            raise _broken(e, "to sign in") from None
        except imaplib.IMAP4.error as e:
            self.close()
            raise GleanError(
                f"the IMAP server refused the sign-in of {user} ({_text(e)}): run `glean mail login` again, and check "
                "that IMAP is on in Outlook's settings (Mail > Forwarding and IMAP)"
            ) from None

    def close(self):
        with suppress(imaplib.IMAP4.error, OSError):
            self.conn.logout()

    def _ok(self, what, command, *args, **kwargs):
        """Send a command; its data if the server answers OK."""
        try:
            typ, data = command(*args, **kwargs)
        except (imaplib.IMAP4.abort, OSError) as e:
            raise _broken(e, what) from None
        except imaplib.IMAP4.error as e:
            typ, data = "NO", [str(e)]
        if typ != "OK":
            raise GleanError(f"the IMAP server refused {what}: {_text(data)}")
        return data

    def folders(self):
        found = []
        for entry in self._ok("to list the folders", self.conn.list):
            if entry is None:
                continue
            line, literal = (entry[0], entry[1]) if isinstance(entry, tuple) else (entry, None)
            m = LIST_LINE.match(line)
            if not m:
                continue
            wire = (literal if literal is not None else _unquote(m.group("name").strip())).decode("utf-8", "replace")
            flags = frozenset(f.lower() for f in m.group("flags").decode("ascii", "replace").split())
            found.append(Folder(decode_folder_name(wire), flags, wire))
        return found

    def _examine(self, folder):
        """Open a folder read-only, and return its UIDVALIDITY."""
        if self.selected != folder.wire:
            self._ok(f"to open {folder.name}", self.conn.select, quote(folder.wire), readonly=True)
            self.selected = None
            _, data = self.conn.response("UIDVALIDITY")
            if not data or data[0] is None:
                raise GleanError(f"the IMAP server gave no UIDVALIDITY for {folder.name}, so glean can't track it")
            self.selected, self.validity = folder.wire, data[0].decode()
        return self.validity

    def scan(self, folder, cursor):
        """Cursor: "UIDVALIDITY:last UID". When the folder's UIDVALIDITY changed, every message counts as new again;
        the state database then finds the ones it knows by their Message-ID."""
        validity = self._examine(folder)
        known_validity, _, last = (cursor or "").partition(":")
        last = int(last) if known_validity == validity and last.isdigit() else 0
        data = self._ok(f"to search {folder.name}", self.conn.uid, "SEARCH", None, f"UID {last + 1}:*")
        numbers = b" ".join(d for d in data if isinstance(d, bytes)).split()  # a server may answer in several lines
        uids = sorted(int(u) for u in numbers if int(u) > last)  # n:* includes the last one
        headers = []
        for start in range(0, len(uids), HEADER_BATCH):
            batch = ",".join(str(u) for u in uids[start : start + HEADER_BATCH])
            data = self._ok(
                f"to read the headers in {folder.name}",
                self.conn.uid,
                "FETCH",
                batch,
                "(UID RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
            )
            for meta, literal in _responses(data):
                uid = UID.search(meta)
                if not uid:
                    continue
                size = SIZE.search(meta)
                when = INTERNALDATE.search(meta)
                found_id = re.search(rb"(?im)^message-id:\s*(.+(?:\r?\n[ \t].+)*)", literal or b"")
                headers.append(
                    Header(
                        ref=f"{validity}:{int(uid.group(1))}",
                        message_id=found_id.group(1).decode("utf-8", "replace").strip() if found_id else None,
                        size=int(size.group(1)) if size else 0,
                        received=internal_date(when.group(1).decode()) if when else None,
                    )
                )
        placed = {int(h.ref.partition(":")[2]) for h in headers}
        unplaced = [u for u in uids if u not in placed]  # the next scan starts again before the first of these
        return headers, f"{validity}:{min(unplaced) - 1 if unplaced else max(uids, default=last)}"

    def fetch(self, folder, headers):
        """Whole messages, in batches of at most BODY_BATCH_BYTES (by their sizes) or BODY_BATCH_COUNT."""
        validity = self._examine(folder)
        batch, total = [], 0
        for header in headers:
            known_validity, _, uid = header.ref.partition(":")
            if known_validity != validity:
                continue  # from before the folder's UIDVALIDITY changed: scan it again
            uid, size = int(uid), header.size
            if batch and (total + size > BODY_BATCH_BYTES or len(batch) >= BODY_BATCH_COUNT):
                yield from self._bodies(folder, validity, batch)
                batch, total = [], 0
            batch.append(uid)
            total += size
        if batch:
            yield from self._bodies(folder, validity, batch)

    def _bodies(self, folder, validity, uids):
        data = self._ok(
            f"to fetch messages in {folder.name}", self.conn.uid, "FETCH", ",".join(map(str, uids)), "(UID BODY.PEEK[])"
        )
        for meta, literal in _responses(data):
            uid = UID.search(meta)
            if uid and literal is not None:
                yield f"{validity}:{int(uid.group(1))}", literal


def _broken(error, what):
    return GleanError(
        f"the connection to the IMAP server broke while trying {what} "
        f"({_text(error) or type(error).__name__}): run `glean mail download` again; it goes on where it stopped"
    )


def _text(value):
    if isinstance(value, list | tuple):
        value = b" ".join(v if isinstance(v, bytes) else str(v).encode() for v in value if v is not None)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value)
