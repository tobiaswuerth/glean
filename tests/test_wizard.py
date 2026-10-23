"""The setup wizard: questions asked again until usable, glean.toml written source by source."""

import pytest

from glean import config, wizard
from glean.errors import GleanError

from .notes_connector import Notes


class Reader:
    """Answers typed at the console, one per prompt; then the end of input."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def reader(*answers):
    return Reader(*answers)


def test_text_asks_again_until_usable():
    said = []
    ask = wizard.Ask(reader("", "nope", "a@b.example"), said.append)
    assert ask.text("Address", check=lambda a: "@" in a) == "a@b.example"
    assert len(said) == 2
    others = reader("")
    assert wizard.Ask(others, said.append).text("Others", default="none") == "none"
    assert others.prompts == ["Others [none]: "]


@pytest.mark.parametrize(
    "answer, default, expected", [("", True, True), ("", False, False), ("j", False, True), ("no", True, False)]
)
def test_yes(answer, default, expected):
    assert wizard.Ask(reader(answer)).yes("Go?", default) is expected


def test_no_answers_at_all():
    with pytest.raises(GleanError, match="run `glean setup` in a terminal"):
        wizard.Ask(reader()).text("Address")


def test_setup_writes_the_chosen_sources(tmp_path):
    said = []
    path = wizard.run(tmp_path, wizard.Ask(reader("y"), said.append), said.append, {"notes": Notes})
    assert path == tmp_path / "glean.toml"
    assert "[notes]" in path.read_text(encoding="utf-8")
    assert "glean notes download" in said[-1]
    with pytest.raises(GleanError, match="unknown setting notes"):
        config.load(path)  # the real registry has no notes source: the file is as the wizard wrote it


def test_an_existing_file_is_kept_unless_replaced(tmp_path):
    (tmp_path / "glean.toml").write_text("kept")
    assert wizard.run(tmp_path, wizard.Ask(reader("")), print, {"notes": Notes}) is None
    assert (tmp_path / "glean.toml").read_text() == "kept"


def test_no_source_chosen(tmp_path):
    with pytest.raises(GleanError, match="no source chosen"):
        wizard.run(tmp_path, wizard.Ask(reader("n")), print, {"notes": Notes})


def test_setup_with_the_mail_source(tmp_path, monkeypatch):
    signed_in = []
    monkeypatch.setattr("glean.connectors.mail.Mail.after_setup", lambda self, ask: signed_in.append(self.name))
    answers = reader("", "me@example.com", "cid-123", "")
    path = wizard.run(tmp_path, wizard.Ask(answers), print)
    cfg = config.load(path)
    assert (cfg.sources["mail"].address, cfg.sources["mail"].client_id) == ("me@example.com", "cid-123")
    assert signed_in == ["mail"]


def test_a_failed_step_after_setup_keeps_the_setup(tmp_path, monkeypatch):
    def refuse(self, ask):
        raise GleanError("the sign-in failed: access_denied")

    monkeypatch.setattr(Notes, "after_setup", refuse)
    said = []
    path = wizard.run(tmp_path, wizard.Ask(reader("y"), said.append), said.append, {"notes": Notes})
    assert path.is_file()
    assert any("the sign-in failed: access_denied" in s and "glean notes" in s for s in said)
    assert "glean notes download" in said[-1]
