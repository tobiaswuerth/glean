"""A source's archive: one folder per item, `YYYY/MM/DD/<id>/`, holding what the download made of it: the original,
the Markdown document made from it and the files saved with it for sift. What the agent's runs make of an item goes
to the home's `runs/` instead, into a folder of the same name (see `run_folder`). File names are safe on Windows,
unique within their folder, and written atomically."""

import json
import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath

RESERVED = re.compile(r"(?i)(con|prn|aux|nul|conin\$|conout\$|com\d|lpt\d)(\..*)?")  # Windows', with any extension
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
NAME_LIMIT = 100  # characters: a full path stays well below Windows' 260
RUNS = "runs"  # in the home: runs.db, what the runs made of each item, and runs/, their transcripts and reports
REPLACE_TRIES, REPLACE_WAIT = 10, 0.2  # Windows refuses to replace a file another program has open, such as an indexer


def local_time(when):
    """`when` in this computer's time zone; as it is where the system can't convert it (Windows, before 1970)."""
    try:
        return when.astimezone()
    except (OverflowError, OSError, ValueError):
        return when


def item_folder(root, when, item_id):
    """The folder of an item dated `when` (as its wall clock shows it)."""
    return Path(root) / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}" / item_id


def run_folder(home, root, path):
    """Where the runs of the item whose original is `path` in the archive `root` keep their files:
    runs/<the archive's name>/<the item's folder in it>/ in the home, runs/mails/2026/10/14/<id>/ for mails/."""
    return Path(home) / RUNS / Path(root).name / PurePosixPath(path).parent


def safe_name(text, limit=NAME_LIMIT):
    """A file name Windows, macOS and Linux all accept, made from any text: unsafe characters become spaces, a
    reserved name gets a leading underscore, and a long name is cut at a word, keeping its extension."""
    name = " ".join(UNSAFE.sub(" ", text).split()).strip(" .")
    if not name:
        return "untitled"
    if RESERVED.fullmatch(name):
        name = "_" + name
    if len(name) > limit:
        stem, dot, ext = name.rpartition(".")
        ext = f".{ext}" if dot and stem and len(ext) <= 8 and " " not in ext else ""
        stem = name[: len(name) - len(ext)]
        cut = stem[: limit - len(ext)]
        if " " in cut.strip():
            cut = cut[: cut.rstrip().rfind(" ")]
        name = cut.strip(" .") + ext
    return name


def unique_name(name, taken):
    """`name`, or with " (2)", " (3)", … before its extension, so that it differs from every name in `taken` (compared
    without case, as Windows and macOS do)."""
    lowered = {t.lower() for t in taken}
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while candidate.lower() in lowered:
        n += 1
        candidate = f"{stem} ({n}){ext}"
    return candidate


def write_atomic(path, data):
    """Write a whole file, or nothing: through a temporary file in the same folder, renamed over the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _replace(src, dst):
    for _ in range(REPLACE_TRIES - 1):
        try:
            return os.replace(src, dst)
        except PermissionError:
            time.sleep(REPLACE_WAIT)
    return os.replace(src, dst)


def front_matter(fields):
    """Flat front matter: one `key: value` line per field, each value written as JSON, which YAML reads too, so that
    quotes, colons and line breaks in a value can't break the block. Lists are JSON arrays."""
    lines = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n"


def relative(root, path):
    """A path as glean's databases keep it: relative to `root` (the archive, or the home's runs/), with forward
    slashes."""
    return Path(path).relative_to(root).as_posix()
