"""An email as a Markdown file: flat front matter with the headers, then the text as written, quoted history and
signatures included (telling them apart is the agent's job). Attachments sift can read are saved beside it."""

from pathlib import Path

from ... import archive
from ..base import Document

ORIGINAL = "message.eml"


def file_name(when, subject):
    """`2026-10-14 0932 Re Offerte Wartungsvertrag.md`: date and subject, which make a good title in sift."""
    stamp = f"{when:%Y-%m-%d %H%M}" if when else "undated"
    return archive.safe_name(f"{stamp} {subject or '(no subject)'}") + ".md"


def write(message, item, folder, save_extensions):
    """Write the email's Markdown document and its attachments into its folder, beside the original."""
    folder = Path(folder)
    when = archive.local_time(message.date) if message.date else None
    name = file_name(when, message.subject)
    taken = {ORIGINAL, name}
    for stale in folder.glob("*.md"):  # a document written before under an older name
        if stale.name != name:
            stale.unlink()
    saved, lines = [], []
    for attachment in message.attachments:
        if attachment.inline:
            continue
        kept = None
        if Path(attachment.name).suffix.lower() in save_extensions:
            kept = archive.unique_name(archive.safe_name(attachment.name), taken)
            taken.add(kept)
            archive.write_atomic(folder / kept, attachment.data)
            saved.append(folder / kept)
        where = f"saved as [{kept}](<{kept}>)" if kept else "not saved"
        lines.append(
            f"- {attachment.name} ({attachment.content_type}, {_size(attachment.size)}, sha256 {attachment.sha256}): "
            f"{where}"
        )
    fields = {
        "type": "email",
        "message_id": message.message_id,
        "date": message.date.isoformat() if message.date else None,
        "folder": item.container,
        "folders": list(item.containers) or [item.container],
        "direction": item.direction,
        "from": ", ".join(str(a) for a in message.from_),
        "to": [str(a) for a in message.to],
        "cc": [str(a) for a in message.cc],
        "bcc": [str(a) for a in message.bcc],
        "sender": str(message.sender) if message.sender else None,
        "reply_to": [str(a) for a in message.reply_to],
        "subject": message.subject,
        "in_reply_to": message.in_reply_to,
        "references": list(message.references),
        "attachments": [a.name for a in message.attachments if not a.inline],
        "original": ORIGINAL,
        "sha256": item.sha256,
    }
    parts = [archive.front_matter(fields), message.text or "(no text)"]
    if lines:
        parts.append("## Attachments\n\n" + "\n".join(lines))
    parts += [_attached(inner) for inner in message.attached]

    text = "\n\n".join(p.strip("\n") for p in parts) + "\n"
    path = folder / name
    archive.write_atomic(path, text.encode("utf-8"))
    return Document(markdown=path, text=text, original=folder / ORIGINAL, sha256=item.sha256, files=tuple(saved))


def _attached(message):
    head = [
        f"From: {', '.join(str(a) for a in message.from_)}",
        f"Date: {message.date.isoformat() if message.date else ''}",
        f"To: {', '.join(str(a) for a in message.to)}",
    ]
    if message.cc:
        head.append(f"Cc: {', '.join(str(a) for a in message.cc)}")
    head.append(f"Subject: {message.subject}")
    sections = [f"## Attached message: {message.subject or '(no subject)'}", "\n".join(head), message.text]
    sections += [_attached(inner) for inner in message.attached]
    return "\n\n".join(s for s in sections if s)


def _size(n):
    if n < 1024:
        return f"{n:,} bytes"
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / (1024 * 1024):.1f} MB"
