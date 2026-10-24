"""glean's configuration, `glean.toml` in GLEAN_HOME: the agent, the MCP servers of orbit and sift, and one table per
source, which that source's connector reads. Relative paths are relative to the file's folder."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import GleanError

EFFORTS = ("low", "medium", "high", "xhigh", "max")
ORBIT_URL = "http://127.0.0.1:8775/mcp/write"  # orbit's tools without its upkeep: merges, renames, lint
MISSING = object()

TEMPLATE = """# glean's configuration. Relative paths are relative to this file's folder.

[agent]
claude = ""              # the claude executable; empty: ~/.local/bin/claude.exe, or claude on PATH
model = "haiku"          # looks at each item first: import, skip or spam; haiku, sonnet, opus or a full model id
import_model = "sonnet"  # imports what is to be imported
effort = ""              # low, medium, high, xhigh or max; empty: the model's default
max_turns = 60           # per item
timeout_minutes = 5      # per item
max_budget_usd = 1.0     # per item; 0: no limit
parallel = 1             # agent runs at once; related items go one after the other while others can run
review_every = 100       # during a run, a review of each this many items, which fixes what they wrote; 0: none
review_model = "opus"    # the model of the review: one run of it for many of the imports' model

[orbit]
url = "http://127.0.0.1:8775/mcp/write"   # /mcp/write: orbit's tools without merges, renames and lint
# vault = "D:/crm"       # orbit's vault; glean asks orbit for it

[sift]
url = "http://127.0.0.1:8765/mcp"
crm_collection = "crm"           # the sift collection of orbit's vault, which glean adds if none holds the vault
document_collections = []        # collections of documents an item may refer to; empty: search them all
# add_collections = false        # don't add collections, or keep the originals out of them: you manage sift
"""


class Table:
    """One table of glean.toml, read setting by setting: each method takes a key and returns its value, checked, or
    the default. `done()` refuses the keys no method asked for."""

    def __init__(self, data, name, path):
        if not isinstance(data, dict):
            raise GleanError(f"{path}: {name} must be a table, [{name}]")
        self.data, self.name, self.file, self.read = data, name, path, set()

    def _get(self, key, default):
        self.read.add(key)
        if key not in self.data:
            if default is MISSING:
                raise self.error(key, "is missing")
            return default
        return self.data[key]

    def error(self, key, problem):
        return GleanError(f"{self.file}: {self.name}.{key} {problem}")

    def text(self, key, default=MISSING):
        value = self._get(key, default)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise self.error(key, "must be text")
        return value.strip()

    def optional_text(self, key):
        """Text, with "" for none."""
        value = self._get(key, "")
        if not isinstance(value, str):
            raise self.error(key, "must be text")
        return value.strip() or None

    def words(self, key, default=(), lower=False):
        value = self._get(key, list(default))
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise self.error(key, "must be a list of text")
        words = (v.strip().lower() if lower else v.strip() for v in value)
        return tuple(dict.fromkeys(w for w in words if w))

    def flag(self, key, default):
        value = self._get(key, default)
        if not isinstance(value, bool):
            raise self.error(key, "must be true or false")
        return value

    def whole(self, key, default):
        value = self._get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise self.error(key, "must be a whole number above 0")
        return value

    def count(self, key, default):
        value = self._get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise self.error(key, "must be a whole number, 0 or more")
        return value

    def port(self, key, default):
        value = self._get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 65536:
            raise self.error(key, "must be a port number")
        return value

    def number(self, key, default, zero_is_none=False):
        value = self._get(key, default)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or value < 0
            or (not zero_is_none and not value)
        ):
            raise self.error(key, "must be a number" + ("" if zero_is_none else " above 0"))
        return None if zero_is_none and not value else value

    def choice(self, key, options):
        value = self.optional_text(key)
        if value is not None and value not in options:
            raise self.error(key, f"must be one of {', '.join(options)}, or empty")
        return value

    def url(self, key, default):
        value = self.text(key, default)
        if not value.startswith(("http://", "https://")):
            raise self.error(key, "must be an http:// or https:// address")
        return value

    def path(self, key, default=None):
        """A path, relative to the file's folder; None for "" or no default."""
        value = self.optional_text(key) if default is None else self.text(key, default)
        return None if value is None else (self.file.parent / Path(value).expanduser()).resolve()

    def done(self):
        if unknown := sorted(set(self.data) - self.read):
            known = ", ".join(sorted(self.read))
            raise GleanError(f"{self.file}: unknown setting {self.name}.{unknown[0]}; known: {known}")


@dataclass(frozen=True)
class Agent:
    claude: Path | None = None
    model: str = "haiku"  # the first look at each item: import, skip or spam
    import_model: str = "sonnet"
    effort: str | None = None
    max_turns: int = 60
    timeout_minutes: float = 5
    max_budget_usd: float | None = 1.0
    parallel: int = 1
    review_every: int = 100  # items per review; 0: no review
    review_model: str = "opus"


@dataclass(frozen=True)
class Sift:
    url: str = "http://127.0.0.1:8765/mcp"
    crm_collection: str = "crm"
    document_collections: tuple[str, ...] = ()
    add_collections: bool = True


@dataclass(frozen=True)
class Config:
    path: Path
    agent: Agent
    orbit_url: str
    orbit_vault: Path | None
    sift: Sift
    sources: dict[str, Any]  # a connector's configuration, by the source's name

    @property
    def home(self):
        return self.path.parent

    def source(self, name):
        if name not in self.sources:
            raise GleanError(f"{name} isn't set up in {self.path}: run `glean setup`")
        return self.sources[name]


def load(path, connectors=None):
    """The configuration in a file, with the defaults for what it leaves out. `connectors`: the sources it may name
    (all of them by default)."""
    if connectors is None:
        from .connectors import CONNECTORS as connectors

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))  # a byte order mark, as PowerShell writes, is fine
    except FileNotFoundError:
        raise GleanError(f"{path} doesn't exist: run `glean setup` to set glean up") from None
    except UnicodeDecodeError:
        raise GleanError(f"{path} isn't UTF-8 text; save it as UTF-8") from None
    except tomllib.TOMLDecodeError as e:
        raise GleanError(f"{path}: {e}") from None
    known = {"agent", "orbit", "sift", *connectors}
    if unknown := sorted(set(data) - known):
        raise GleanError(f"{path}: unknown setting {unknown[0]}; known: {', '.join(sorted(known))}")
    tables = {name: Table(data.get(name, {}), name, path) for name in ("agent", "orbit", "sift")}
    t = tables["agent"]
    agent = Agent(
        claude=t.path("claude"),
        model=t.text("model", Agent.model),
        import_model=t.text("import_model", Agent.import_model),
        effort=t.choice("effort", EFFORTS),
        max_turns=t.whole("max_turns", Agent.max_turns),
        timeout_minutes=t.number("timeout_minutes", Agent.timeout_minutes),
        max_budget_usd=t.number("max_budget_usd", Agent.max_budget_usd, zero_is_none=True),
        parallel=t.whole("parallel", Agent.parallel),
        review_every=t.count("review_every", Agent.review_every),
        review_model=t.text("review_model", Agent.review_model),
    )
    orbit_url = tables["orbit"].url("url", ORBIT_URL)
    orbit_vault = tables["orbit"].path("vault")
    t = tables["sift"]
    sift = Sift(
        url=t.url("url", Sift.url),
        crm_collection=t.text("crm_collection", Sift.crm_collection),
        document_collections=t.words("document_collections"),
        add_collections=t.flag("add_collections", Sift.add_collections),
    )
    for table in tables.values():
        table.done()
    sources = {}
    for name, connector in connectors.items():
        if name in data:
            table = Table(data[name], name, path)
            sources[name] = connector.configure(table)
            table.done()
    return Config(path=path, agent=agent, orbit_url=orbit_url, orbit_vault=orbit_vault, sift=sift, sources=sources)


def template(sources):
    """The glean.toml the setup wizard writes: the shared settings, then each source's table as its connector wrote
    it."""
    return TEMPLATE + "".join(f"\n{text.strip()}\n" for text in sources.values())
