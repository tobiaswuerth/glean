"""`glean setup`, also shown on first use: asks what glean.toml needs, source by source, writes it, and lets each
source finish its setup, such as signing in."""

from . import archive, config, console
from .connectors import CONNECTORS
from .errors import GleanError


class Ask:
    """Questions on the console, asked again until the answer is usable."""

    def __init__(self, read=input, say=console.say):
        self.read, self.say = read, say

    def _read(self, prompt):
        try:
            return self.read(prompt).strip()
        except EOFError:
            raise GleanError("the setup needs answers: run `glean setup` in a terminal") from None

    def text(self, prompt, default=None, check=None):
        while True:
            answer = self._read(f"{prompt}{f' [{default}]' if default else ''}: ")
            if not answer and default is not None:
                return default
            if answer and (check is None or check(answer)):
                return answer
            self.say("  That isn't a usable answer; please try again.")

    def yes(self, prompt, default=True):
        answer = self._read(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").lower()
        return default if not answer else answer in ("y", "yes", "j", "ja")


def run(home, ask=None, say=console.say, connectors=CONNECTORS):
    """Set glean up in `home`; returns the path of glean.toml, or None if the user kept the one there."""
    ask = ask or Ask(say=say)
    path = home / "glean.toml"
    say(f"Setting up glean in {home}. (GLEAN_HOME chooses another folder.)")
    if path.exists() and not ask.yes(f"{path} exists already. Replace it?", default=False):
        return None
    tables = {}
    for name, connector in connectors.items():
        if ask.yes(f"Import {connector.help}?", default=True):
            tables[name] = connector.setup(ask, say)
    if not tables:
        raise GleanError("no source chosen, so there is nothing to set up")
    archive.write_atomic(path, config.template(tables).encode("utf-8"))
    say(f"Wrote {path}; edit it for more settings.")
    cfg = config.load(path, connectors)
    for name in tables:
        try:
            connectors[name](cfg.sources[name], home, say).after_setup(ask)
        except GleanError as e:
            say(f"{name}: {e}. The setup is saved; finish this step later with `glean {name} …`.")
    first = next(iter(tables))
    say(
        "\nNext:\n"
        f"  glean doctor            checks Claude Code, orbit and sift\n"
        f"  glean {first} download   downloads what is new\n"
        f"  glean {first} list       shows what is waiting to be imported\n"
        f"  glean {first} run --oldest 5   imports the 5 oldest: orbit grows as your mailbox did"
    )
    return path
