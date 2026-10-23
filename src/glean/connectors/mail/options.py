"""The `[mail]` table of glean.toml: the account and how to reach it, which folders to read, the senders to skip, and
where the mail is kept. A list of folders adds to glean's own, which come with every version."""

import json
from dataclasses import dataclass
from pathlib import Path

from ...config import MISSING

CONSUMERS = "https://login.microsoftonline.com/consumers"  # personal Microsoft accounts: outlook.com, hotmail.com
EXCLUDE_FOLDERS = ("junk", "deleted", "drafts", "outbox", "notes")  # besides what the server marks junk, trash, drafts
SAVE_ATTACHMENTS = (".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".rtf", ".epub", ".txt", ".md")
TEMPLATE = """[mail]
address = {address}
client_id = {client_id}   # the Application (client) ID of your Microsoft Entra app
my_addresses = {my_addresses}   # your other addresses: mail from them is outbound too
archive = "mails"           # where mail is kept, and mails.db beside it: relative to this file, or absolute
collection = "mails"        # its sift collection, which glean adds if no collection holds the archive yet
# Only if glean's own choices don't fit:
# folders = ["Inbox", "Sent"]         read only these folders
# exclude_folders = ["Newsletters"]   leave these out too (glean leaves out junk, trash, drafts, outbox and notes)
# skip_senders = ["bot@example.com"]  skip mail from these without an agent run (glean skips none by itself)
# eml_folder = "D:/saved mail"        read saved .eml files from this folder instead of the IMAP server
"""


@dataclass(frozen=True)
class MailOptions:
    address: str
    client_id: str | None
    authority: str
    redirect_uri: str
    host: str
    port: int
    folders: tuple[str, ...]
    exclude_folders: tuple[str, ...]  # lower case, glean's and the configured
    my_addresses: tuple[str, ...]  # lower case; the account's first
    archive: Path
    collection: str
    save_attachments: tuple[str, ...]  # extensions, lower case, with their dot
    skip_senders: tuple[str, ...]  # the owner's: mail from them is skipped without a run; patterns, lower case
    eml_folder: Path | None


def _more(defaults, extra):
    return tuple(dict.fromkeys((*defaults, *extra)))


def configure(table):
    address = table.text("address").lower()
    if "@" not in address or " " in address:
        raise table.error("address", "must be an email address")
    for gone in ("skip_bulk", "skip_spam"):
        if gone in table.data:
            raise table.error(
                gone, "is gone: what the headers say is evidence for the agent now, which decides; remove it"
            )
    eml_folder = table.path("eml_folder")
    client_id = table.text("client_id", None if eml_folder else MISSING)
    return MailOptions(
        address=address,
        client_id=client_id,
        authority=table.url("authority", CONSUMERS),
        redirect_uri=table.url("redirect_uri", "http://localhost"),
        host=table.text("host", "outlook.office365.com"),
        port=table.port("port", 993),
        folders=table.words("folders"),
        exclude_folders=_more(EXCLUDE_FOLDERS, table.words("exclude_folders", lower=True)),
        my_addresses=_more((address,), table.words("my_addresses", lower=True)),
        archive=table.path("archive", "mails"),
        collection=table.text("collection", "mails"),
        save_attachments=tuple(
            e if e.startswith(".") else f".{e}" for e in table.words("save_attachments", SAVE_ATTACHMENTS, lower=True)
        ),
        skip_senders=table.words("skip_senders", lower=True),
        eml_folder=eml_folder,
    )


def template(address, client_id, my_addresses=()):
    def toml(value):
        return json.dumps(value, ensure_ascii=False)  # JSON strings and arrays are TOML too

    return TEMPLATE.format(address=toml(address), client_id=toml(client_id), my_addresses=toml(list(my_addresses)))
