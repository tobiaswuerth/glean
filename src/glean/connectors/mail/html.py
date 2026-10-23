"""HTML mail to text, with inscriptis (Apache-2.0): tables and lists keep their layout, links keep their address,
styles and scripts are dropped."""

import re

from inscriptis import get_text
from inscriptis.model.config import ParserConfig

CONFIG = ParserConfig(display_links=True)
XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u034f]")  # zero-width characters mailers pad with
BLANK_LINES = re.compile(r"\n[ \t]*(?:\n[ \t]*){2,}")


def to_text(html):
    text = get_text(XML_DECLARATION.sub("", html), CONFIG) if html.strip() else ""
    lines = (line.rstrip() for line in INVISIBLE.sub("", text).splitlines())
    return BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
