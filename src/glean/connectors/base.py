"""What every connector provides. A connector knows one kind of source: how to set it up, how to download what it
holds into its archive, and how to turn one item into the Markdown document the agent reads. Everything else, the
state database, choosing items, the agent run and recording its outcome, is shared and lives outside the
connectors."""

from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from .. import console


@dataclass(frozen=True)
class Document:
    """An item, normalized: the Markdown file that is the agent's input, sift's document and orbit's source_file."""

    markdown: Path
    text: str  # the Markdown file's content
    original: Path  # the item as downloaded
    sha256: str  # of the original
    files: tuple[Path, ...] = ()  # files saved beside the Markdown for sift to index, such as attachments
    related: frozenset[str] = frozenset()  # what it shares with items it mustn't run beside: people, a thread
    signals: tuple[str, ...] = ()  # what its headers and sender say about who wrote it, as evidence for the agent
    addresses: tuple[str, ...] = ()  # its sender's and recipients', lower case: its interaction must name each


@dataclass
class Downloaded:
    """What a download found."""

    new: int = 0  # items stored for the first time
    skipped: int = 0  # of those, how many the owner's skip_senders set aside
    known: int = 0  # items seen before, possibly in another place
    places: dict[str, int] = field(default_factory=dict)  # new items per container


@dataclass(frozen=True)
class Command:
    """A command of the connector's own, besides the shared ones: `glean <source> <name>`."""

    help: str
    run: Callable[["Connector", Namespace], int]
    arguments: Callable[[ArgumentParser], None] = lambda parser: None


class Connector(ABC):
    name: ClassVar[str]  # its command group, its table in glean.toml, and the `source` of its items: "mail"
    noun: ClassVar[str]  # what one item is called, for people and for the agent: "email"
    help: ClassVar[str]  # one line for `glean --help`
    commands: ClassVar[dict[str, Command]] = {}
    ignored: ClassVar[tuple[str, ...]] = ()  # files in its archive sift should leave out, in .siftignore's syntax

    def __init__(self, config, home, say=console.say):
        self.config, self.home, self.say = config, Path(home), say

    @classmethod
    @abstractmethod
    def configure(cls, table):
        """Read its table of glean.toml (a config.Table) into its configuration, which has at least `archive` (a
        folder) and `collection` (the sift collection of that folder)."""

    @classmethod
    @abstractmethod
    def setup(cls, ask, say):
        """Ask what its table in glean.toml needs, and return that table as TOML text."""

    def after_setup(self, ask):  # noqa: B027 (optional)
        """Anything to do once glean.toml is written, such as signing in."""

    @property
    def archive(self):
        return self.config.archive

    @property
    def collection(self):
        return self.config.collection

    def key(self, text):
        """An item's key as written by a person, as the state database stores it."""
        return text

    def upgrade(self, state):  # noqa: B027 (optional)
        """Bring what an older glean stored in line with this version, such as items its rules judged otherwise;
        called before each download, list and run, so it should do its work once per version."""

    def check(self):
        """Problems with its setup, for `glean doctor`: a list of (ok, line)."""
        return []

    @abstractmethod
    def download(self, state):
        """Store every item the source holds that the state database doesn't know yet, in the archive and the state
        database, and return what it found as Downloaded. Never changes the source."""

    @abstractmethod
    def document(self, item):
        """Write the item's Markdown document, and the files sift should index with it, beside its original in the
        archive. Returns a Document. Writing it again gives the same files; a connector may write it when it
        downloads the item already, so that sift can search everything downloaded."""

    @abstractmethod
    def instructions(self):
        """What the agent does with an item of this kind: the source's part of the agent's system prompt."""
