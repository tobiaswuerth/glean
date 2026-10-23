"""What glean tells the user while it works. It all goes to stderr, so stdout stays for the CLI's results."""

import sys


def say(*parts):
    print(*parts, file=sys.stderr, flush=True)


def count(n, noun, plural=None):
    """'1 email', '2 emails', '12,345 emails'."""
    return f"{n:,} {noun if n == 1 else plural or noun + 's'}"
