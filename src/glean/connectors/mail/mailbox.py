"""The small interface the mail connector reads mail through: list the folders, list what is new in one, fetch raw
messages. IMAP is one implementation; a folder of saved .eml files is another. Nothing here changes a mailbox."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ...errors import GleanError


@dataclass(frozen=True)
class Folder:
    name: str  # as people read it
    flags: frozenset[str] = frozenset()  # lower case, such as "\\sent", "\\junk", "\\noselect"
    wire: str = ""  # as the server spells it, if that differs


@dataclass(frozen=True)
class Header:
    ref: str  # where the message is in its folder, stable while the folder is: an IMAP UID with its UIDVALIDITY
    message_id: str | None  # as the header gives it; None if the mailbox can't tell without fetching
    size: int
    received: datetime | None  # when the mailbox got it


class Mailbox(ABC):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):  # noqa: B027 (optional)
        pass

    @abstractmethod
    def folders(self):
        """Every folder, as Folder."""

    @abstractmethod
    def scan(self, folder, cursor):
        """The messages of a folder added since `cursor` (None: all of them), as Headers, and the new cursor."""

    @abstractmethod
    def fetch(self, folder, headers):
        """Yield (ref, raw bytes) for the messages of these Headers, in batches as the mailbox likes."""


class EmlFolder(Mailbox):
    """A folder of saved .eml files, each subfolder a mail folder; files directly in it are in "Inbox"."""

    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_dir():
            raise GleanError(f"{self.root} isn't a folder: check mail.eml_folder in glean.toml")

    def _dir(self, folder):
        return self.root if folder.name == "Inbox" else self.root / folder.name

    def folders(self):
        names = {"Inbox"} | {p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")}
        return [Folder(n) for n in sorted(names)]

    def scan(self, folder, cursor):
        found = []
        for path in sorted(self._dir(folder).iterdir()):
            if path.suffix.lower() != ".eml" or not path.is_file():
                continue
            info = path.stat()
            found.append(Header(path.name, None, info.st_size, datetime.fromtimestamp(info.st_mtime, UTC)))
        return found, None

    def fetch(self, folder, headers):
        for header in headers:
            try:
                raw = (self._dir(folder) / header.ref).read_bytes()
            except FileNotFoundError:  # removed since the scan: it didn't arrive
                continue
            yield header.ref, raw
