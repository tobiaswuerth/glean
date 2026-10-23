"""What glean tells the user while it works: results on stdout, everything else on stderr; both also to the log of
the download, run or review at work, if there is one (see logs)."""

import logging
import sys

log = logging.getLogger("glean")


def say(*parts):
    print(*parts, file=sys.stderr, flush=True)
    log.info(" ".join(map(str, parts)))


def show(line):
    """A result line, such as an item's outcome."""
    print(line, flush=True)
    log.info(line)


def count(n, noun, plural=None):
    """'1 email', '2 emails', '12,345 emails'."""
    return f"{n:,} {noun if n == 1 else plural or noun + 's'}"
