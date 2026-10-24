"""glean.toml: the shared settings (agent, orbit, sift), one table per source, and messages that say what to fix."""

import pytest

from glean import config
from glean.errors import GleanError

MAIL = """
[mail]
address = "tobias@example.com"
client_id = "11111111-2222-3333-4444-555555555555"
"""


def write(tmp_path, text):
    path = tmp_path / "glean.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shared_settings_have_defaults(tmp_path):
    c = config.load(write(tmp_path, MAIL))
    assert c.home == tmp_path
    assert c.agent.model == "haiku"
    assert c.agent.max_turns == 60
    assert c.agent.timeout_minutes == 5
    assert c.agent.max_budget_usd == 1.0
    assert c.agent.effort is None
    assert c.agent.claude is None
    assert c.agent.parallel == 1
    assert (c.agent.model, c.agent.import_model) == ("haiku", "sonnet")  # Haiku judges, Sonnet imports
    assert (c.agent.review_every, c.agent.review_model) == (100, "opus")
    assert c.orbit_url == "http://127.0.0.1:8775/mcp/write"  # orbit's tools without upkeep
    assert c.orbit_vault is None  # asked from orbit
    assert c.sift.url == "http://127.0.0.1:8765/mcp"
    assert (c.sift.crm_collection, c.sift.document_collections, c.sift.add_collections) == ("crm", (), True)


def test_every_shared_setting_can_be_given(tmp_path):
    text = MAIL + (
        '[agent]\nclaude = "bin/claude.exe"\nmodel = "opus"\neffort = "high"\nmax_turns = 10\n'
        'timeout_minutes = 2.5\nmax_budget_usd = 0\nparallel = 4\nreview_every = 0\nreview_model = "sonnet"\n'
        'import_model = "opus"\n'
        '[orbit]\nurl = "http://127.0.0.1:8776/mcp"\nvault = "crm"\n'
        '[sift]\nurl = "http://127.0.0.1:9000/mcp"\ncrm_collection = "vault"\ndocument_collections = ["docs"]\n'
        "add_collections = false\n"
    )
    c = config.load(write(tmp_path, text))
    assert c.agent.claude == tmp_path / "bin/claude.exe"
    assert (c.agent.model, c.agent.effort, c.agent.max_turns) == ("opus", "high", 10)
    assert c.agent.timeout_minutes == 2.5
    assert c.agent.max_budget_usd is None  # 0: no limit
    assert c.agent.parallel == 4
    assert (c.agent.review_every, c.agent.review_model) == (0, "sonnet")  # 0: no review
    assert c.agent.import_model == "opus"
    assert c.orbit_url.endswith(":8776/mcp")
    assert c.orbit_vault == tmp_path / "crm"
    assert c.sift.url.endswith(":9000/mcp")
    assert (c.sift.crm_collection, c.sift.document_collections, c.sift.add_collections) == ("vault", ("docs",), False)


def test_each_source_table_is_read_by_its_connector(tmp_path):
    c = config.load(write(tmp_path, MAIL))
    assert list(c.sources) == ["mail"]
    assert c.sources["mail"].address == "tobias@example.com"
    assert c.source("mail").archive == tmp_path / "mails"


def test_a_source_without_a_table_is_not_set_up(tmp_path):
    c = config.load(write(tmp_path, "[agent]\n"))
    assert c.sources == {}
    with pytest.raises(GleanError, match=r"mail isn't set up.*glean setup"):
        c.source("mail")


def test_a_missing_file_says_to_run_setup(tmp_path):
    with pytest.raises(GleanError, match="glean setup"):
        config.load(tmp_path / "glean.toml")


def test_a_byte_order_mark_is_fine(tmp_path):
    path = tmp_path / "glean.toml"
    path.write_text(MAIL, encoding="utf-8-sig")
    assert config.load(path).sources["mail"].address == "tobias@example.com"


@pytest.mark.parametrize(
    "text, message",
    [
        ("[agents]\n", "unknown setting agents; known: agent, mail, orbit, sift"),
        ("[agent]\ncolour = 1\n", "unknown setting agent.colour"),
        ("[agent]\nmax_turns = 0\n", "agent.max_turns must be a whole number above 0"),
        ("[agent]\nmax_turns = 2.5\n", "agent.max_turns must be a whole number above 0"),
        ("[agent]\ntimeout_minutes = -1\n", "agent.timeout_minutes must be a number above 0"),
        ("[agent]\nmax_budget_usd = 'lots'\n", "agent.max_budget_usd must be a number"),
        ("[agent]\nmax_budget_usd = -1\n", "agent.max_budget_usd must be a number"),
        ("[agent]\neffort = 'extreme'\n", "agent.effort must be one of"),
        ("[agent]\nmodel = ''\n", "agent.model must be text"),
        ("[sift]\nadd_collections = 'no'\n", "sift.add_collections must be true or false"),
        ("[agent]\nreview_every = -1\n", "agent.review_every must be a whole number, 0 or more"),
        ("[orbit]\nurl = 'ftp://x'\n", "orbit.url must be an http"),
        ("[sift]\ndocument_collections = 'docs'\n", "sift.document_collections must be a list of text"),
        ("agent = 3\n", "agent must be a table"),
        ("[agent\n", "glean.toml"),
    ],
)
def test_bad_settings_are_named(tmp_path, text, message):
    with pytest.raises(GleanError, match=message):
        config.load(write(tmp_path, text))


def test_a_file_that_is_not_utf8_is_named(tmp_path):
    path = tmp_path / "glean.toml"
    path.write_bytes(b"[agent]\nmodel = '\xe4'\n")
    with pytest.raises(GleanError, match="isn't UTF-8"):
        config.load(path)


def test_the_template_loads_as_written(tmp_path):
    text = config.template({"mail": "[mail]\naddress = 'me@example.com'\nclient_id = 'abc'\n"})
    c = config.load(write(tmp_path, text))
    assert c.agent.model == "haiku"
    assert c.sources["mail"].client_id == "abc"
