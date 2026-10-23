"""A minimal connector for the tests of the shared parts: its source is a dict of notes. That it takes this little to
add a source is part of what the tests check."""

import hashlib
from typing import ClassVar

from glean import archive
from glean.connectors import Connector, Document, Downloaded
from glean.state import Item


class Options:
    def __init__(self, root):
        self.archive, self.collection = root, "notes"


class Notes(Connector):
    name = "notes"
    noun = "note"
    help = "notes from a dict, for the tests"
    source: ClassVar[dict] = {}  # key: (date, title, text); a test sets its own on the instance

    @classmethod
    def configure(cls, table):
        return Options(table.path("archive", "notes"))

    @classmethod
    def setup(cls, ask, say):
        return '[notes]\narchive = "notes"\n'

    def download(self, state, oldest=None, latest=None):
        report = Downloaded()
        new = sorted((date, key) for key, (date, _, _) in self.source.items() if not state.find(self.name, key))
        report.known = len(self.source) - len(new)
        count = oldest or latest
        taken = new if count is None else new[:count] if oldest else new[-count:]
        report.waiting = len(new) - len(taken)
        for date, key in taken:
            _, title, text = self.source[key]
            item_id = state.new_id(self.name, key)
            path = self.archive / item_id / "original.txt"
            archive.write_atomic(path, text.encode("utf-8"))
            item = Item(self.name, key, date, title, "someone", "all", None, archive.relative(self.archive, path),
                        hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text), id=item_id)  # fmt: skip
            state.add(item, "all", key)
            report.new += 1
        return report

    def document(self, item):
        original = self.archive / item.path
        text = original.read_text(encoding="utf-8")
        if text == "unreadable":
            raise ValueError("can't read it")
        markdown = original.with_name(f"{item.title}.md")
        archive.write_atomic(markdown, f"# {item.title}\n\n{text}\n".encode())
        return Document(markdown, markdown.read_text(encoding="utf-8"), original, item.sha256)

    def instructions(self):
        return "## Importing a note\n\nLog it."
