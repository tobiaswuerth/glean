"""The agent run: Claude Code started sandboxed, its tools checked before it acts, its result read whatever happens."""

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from glean import agent, config
from glean.errors import GleanError

from . import fake_claude

FAKE = str(Path(__file__).with_name("fake_claude.py"))


def cfg(tmp_path, extra=""):
    path = tmp_path / "glean.toml"
    path.write_text("[agent]\nmax_turns = 9\n" + extra, encoding="utf-8")
    return config.load(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Run the fake claude in a mode; returns what it logged about each start."""
    log = tmp_path / "claude.log"

    def use(mode="ok", **spec):
        monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": mode, "log": str(log), **spec}))
        return lambda: [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return use


def make(tmp_path, extra=""):
    return agent.Agent(cfg(tmp_path, extra), command=[sys.executable, FAKE])


def test_prepare_learns_which_tools_to_hide(tmp_path, fake):
    starts = fake()
    a = make(tmp_path)
    assert a.prepare() == list(agent.ALLOWED)
    assert a.disallowed == sorted(
        [
            "mcp__orbit__merge_entities",
            "mcp__orbit__rename_entity",
            "mcp__orbit__lint_vault",
            "mcp__orbit__settle_review",
            "mcp__sift__vsearch",
        ]
    )
    (start,) = starts()
    assert start["mcp"] == {
        "mcpServers": {
            "orbit": {"type": "http", "url": "http://127.0.0.1:8775/mcp/write"},
            "sift": {"type": "http", "url": "http://127.0.0.1:8765/mcp"},
        }
    }


def test_a_run_is_sandboxed_and_fed_on_stdin(tmp_path, fake):
    starts = fake()
    a = make(tmp_path, 'effort = "high"\nmax_budget_usd = 0.5\n')
    transcript = tmp_path / "t.jsonl"
    result = a.run("SYSTEM PROMPT", "Import this.\n\nGrüezi", transcript)
    assert result.ok
    assert (result.outcome, result.turns, result.cost_usd, result.duration_ms) == ("success", 7, 0.12, 4321)
    assert result.report == "**Interaction**: int_1"
    assert result.reason is None
    probe, run = starts()
    args = run["args"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--allowedTools") + 1].split(",") == [*agent.ALLOWED, agent.ANSWER]
    assert args[args.index("--disallowedTools") + 1].split(",") == a.disallowed
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--permission-prompts") + 1] == "none"
    for flag in ("--restricted", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--output-format") + 1] == args[args.index("--input-format") + 1] == "stream-json"
    assert args[args.index("--max-turns") + 1] == "9"
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--max-budget-usd") + 1] == "0.5"
    assert args[args.index("--effort") + 1] == "high"
    assert run["stdin"] == "Import this.\n\nGrüezi"
    assert run["prompt"] == "SYSTEM PROMPT"
    assert run["cwd_files"] == []  # an empty folder: no project files or settings
    assert run["cwd"] != probe["cwd"]
    events = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert (events[0]["type"], events[-1]["type"], result.followups, result.checks) == ("system", "result", 0, [])


def test_a_review_run_has_orbit_s_upkeep_its_own_answer_model_and_limits(tmp_path, fake):
    starts = fake("review")
    a = agent.Agent(cfg(tmp_path, 'review_model = "opus"\nmax_budget_usd = 0.5\n'), command=[sys.executable, FAKE],
                    role=agent.REVIEW)  # fmt: skip
    result = a.run("REVIEW PROMPT", "digests")
    assert (result.ok, result.answer["reimport"], result.report) == (True, ["e1"], "Merged two Hans.")
    _, run = starts()
    args = run["args"]
    allowed = args[args.index("--allowedTools") + 1].split(",")
    assert "mcp__orbit__merge_entities" in allowed
    assert "mcp__orbit__settle_review" in allowed  # it acts on the owner's answers
    assert "mcp__orbit__log_interaction" not in allowed
    assert json.loads(args[args.index("--json-schema") + 1]) == agent.REVIEW.schema
    assert (args[args.index("--model") + 1], args[args.index("--max-turns") + 1]) == ("opus", "90")
    assert args[args.index("--max-budget-usd") + 1] == "5.0"
    assert run["mcp"]["mcpServers"]["orbit"]["url"] == "http://127.0.0.1:8775/mcp"  # with merges


def test_orbit_s_endpoint_for_each_role():
    assert agent.endpoint("http://h:1/mcp/write", "/mcp") == "http://h:1/mcp"
    assert agent.endpoint("http://h:1/mcp", "/mcp/write") == "http://h:1/mcp/write"
    assert agent.endpoint("http://proxy/orbit", "/mcp") == "http://proxy/orbit"  # a URL of the owner's own: as is


def test_no_budget_and_no_effort_by_default(tmp_path, fake):
    starts = fake()
    make(tmp_path, "max_budget_usd = 0\n").run("p", "m")
    args = starts()[-1]["args"]
    assert "--max-budget-usd" not in args
    assert "--effort" not in args


def test_a_run_with_other_tools_is_stopped(tmp_path, fake):
    fake("hang")
    a = make(tmp_path)
    a.disallowed = []  # as if the servers' other tools weren't hidden
    started = time.monotonic()
    result = a.run("p", "m")
    assert time.monotonic() - started < 30
    assert (result.outcome, result.ok) == ("refused", False)
    assert "mcp__orbit__merge_entities" in result.reason


def test_a_run_whose_server_is_down_is_stopped(tmp_path, fake):
    fake("offline")
    a = make(tmp_path)
    a.disallowed = list(fake_claude.OTHERS)  # hidden, as prepare() would
    result = a.run("p", "m")
    assert result.outcome == "refused"
    assert "sift isn't connected (failed)" in result.reason


@pytest.mark.parametrize(
    "mode, message",
    [
        ("offline", "sift isn't connected (failed): is `sift serve` running at http://127.0.0.1:8765/mcp?"),
        ("builtin", "offered tools glean doesn't allow: Bash"),
        ("missing", "lack tools glean needs: mcp__orbit__propose_review"),
        ("crash", "didn't start: boom: something broke"),
        ("silent", "didn't start: exit code 0"),
    ],
)
def test_prepare_refuses_a_setup_that_is_not_right(tmp_path, fake, mode, message):
    fake(mode)
    with pytest.raises(GleanError, match=message.replace("?", r"\?").replace("(", r"\(").replace(")", r"\)")):
        make(tmp_path).prepare()


def test_a_run_that_runs_out_of_turns(tmp_path, fake):
    fake("max_turns")
    result = make(tmp_path).run("p", "m")
    assert (result.outcome, result.ok, result.turns, result.cost_usd) == ("error_max_turns", False, 60, 0.9)
    assert result.reason == "Reached maximum number of turns (60)"


def test_an_error_reported_as_text(tmp_path, fake):
    fake("error_text")
    result = make(tmp_path).run("p", "m")
    assert (result.ok, result.reason) == (False, "API Error: overloaded")


def test_a_run_that_takes_too_long(tmp_path, fake):
    fake("hang")
    a = make(tmp_path, "timeout_minutes = 0.02\n")
    a.prepare()
    result = a.run("p", "m")
    assert (result.outcome, result.ok) == ("timeout", False)
    assert "no result within 0.02 minutes" in result.reason


def test_a_run_that_crashes(tmp_path, fake):
    fake("ok")
    a = make(tmp_path)
    a.prepare()
    fake("crash")
    result = a.run("p", "m")
    assert (result.outcome, result.ok) == ("crashed", False)
    assert "boom: something broke" in result.reason


def test_lines_that_are_not_json_are_skipped(tmp_path, fake):
    fake("garbage")
    assert make(tmp_path).run("p", "m").ok


def test_denied_tool_calls_are_reported(tmp_path, fake):
    fake("denied")
    result = make(tmp_path).run("p", "m")
    assert result.denied == [{"tool_name": "mcp__orbit__merge_entities", "tool_input": {}}]


def test_a_run_that_does_not_exit_after_its_result_is_ended(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(agent, "EXIT_SECONDS", 0.2)
    fake("linger")
    started = time.monotonic()
    assert make(tmp_path).run("p", "m").ok
    assert time.monotonic() - started < 30


def test_ctrl_c_stops_the_run(tmp_path, fake, monkeypatch):
    fake("hang")
    a = make(tmp_path)
    a.prepare()
    monkeypatch.setattr(agent.queue.Queue, "get", lambda self, timeout: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        a.run("p", "m")


def test_a_command_that_cannot_start(tmp_path):
    a = agent.Agent(cfg(tmp_path), command=[str(tmp_path / "missing.exe")])
    with pytest.raises(GleanError, match="can't start Claude Code"):
        a.prepare()


def test_sending_to_a_process_that_is_gone():
    class Closed:
        def write(self, data):
            raise BrokenPipeError

    agent._send(Closed(), "x")


def test_a_run_that_left_work_is_sent_back_and_does_it(tmp_path, fake):
    # The agent answered "imported" without logging the interaction; glean's checks send it back, in its session.
    fake("sloppy")
    result = make(tmp_path).run("p", "m", tmp_path / "t.jsonl")
    (followup,) = [json.loads(line) for line in (tmp_path / "claude.log.followups").read_text("utf-8").splitlines()]
    assert followup.startswith("glean checked your import, and this is left to do:\n- log_interaction never succeeded")
    assert (result.ok, result.followups, result.checks) == (True, 1, [])
    assert result.sent_back[0][0].startswith("log_interaction never succeeded")
    assert (result.turns, result.cost_usd, result.duration_ms) == (14, 0.24, 8642)  # the cost is the session's
    events = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text("utf-8").splitlines()]
    (sent,) = [e for e in events if e.get("type") == "user" and e["message"]["content"] == followup]
    assert sent["glean"] == "followup"  # the transcript has what glean said, which Claude Code doesn't echo


def test_a_run_that_leaves_work_is_sent_back_twice_and_then_kept(tmp_path, fake):
    fake("stubborn")
    result = make(tmp_path).run("p", "m")
    assert (result.ok, result.followups, result.turns) == (True, agent.FOLLOWUPS, 21)
    assert result.checks[0].startswith("log_interaction never succeeded")


def test_the_addresses_an_interaction_must_name_are_checked(tmp_path, fake):
    fake("ok")  # logs the interaction with none of them
    result = make(tmp_path).run("p", "m", addresses=("anna@acme.example",))
    assert result.checks[0].startswith("anna@acme.example is in the email's From, To or Cc")


def test_a_skipped_email_is_not_sent_back(tmp_path, fake):
    fake("skip")
    assert make(tmp_path).run("p", "m").followups == 0


def test_finding_claude(tmp_path, monkeypatch):
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    assert agent.find_claude(exe) == exe
    with pytest.raises(GleanError, match="doesn't exist"):
        agent.find_claude(tmp_path / "nope.exe")
    script = tmp_path / "claude.cmd"
    script.write_bytes(b"")
    with pytest.raises(GleanError, match="script wrapper"):
        agent.find_claude(script)
    monkeypatch.setattr(agent.Path, "home", lambda: tmp_path / "nohome")
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    with pytest.raises(GleanError, match="isn't installed"):
        agent.find_claude()
    monkeypatch.setattr(agent.shutil, "which", lambda name: str(exe))
    assert agent.find_claude() == exe
    native = tmp_path / "nohome" / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
    native.parent.mkdir(parents=True)
    native.write_bytes(b"")
    assert agent.find_claude() == native


def test_an_agent_finds_claude_itself(tmp_path, monkeypatch):
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    a = agent.Agent(cfg(tmp_path, f"claude = {json.dumps(str(exe))}\n"))
    assert a.base == [str(exe)]


@pytest.mark.parametrize(
    "text, parsed", [("2.1.303 (Claude Code)\n", (2, 1, 303)), ("2.1.0", (2, 1, 0)), ("unknown", ()), ("", ())]
)
def test_versions(text, parsed):
    assert agent.version(text) == parsed


class Endless:
    """A process's output that never pauses."""

    def __init__(self):
        self.stopped = False

    def __iter__(self):
        line = json.dumps({"type": "assistant", "message": {}}).encode() + b"\n"
        while not self.stopped:
            yield line

    def close(self):
        self.stopped = True


class Talker:
    """A process that keeps talking and never gives a result, until it is killed."""

    def __init__(self, *args, **kwargs):
        self.stdin, self.stdout, self.stderr, self.returncode = io.BytesIO(), Endless(), io.BytesIO(), None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None and timeout is not None:
            raise subprocess.TimeoutExpired("claude", timeout)
        return self.returncode

    def kill(self):
        self.returncode = -9
        self.stdout.stopped = True


@pytest.mark.timeout(30)
def test_a_run_that_keeps_talking_past_its_time_is_stopped(tmp_path):
    a = agent.Agent(cfg(tmp_path, "timeout_minutes = 0.02\n"), command=["claude"], popen=Talker)
    a.disallowed = []
    result = a.run("p", "m")
    assert result.outcome == "timeout"


def test_a_transcript_that_cannot_be_written_starts_nothing(tmp_path, fake):
    fake()
    a = make(tmp_path)
    a.prepare()
    started = []
    real = a.popen
    a.popen = lambda *args, **kwargs: started.append(args) or real(*args, **kwargs)
    with pytest.raises((PermissionError, IsADirectoryError)):  # Windows says the one, POSIX the other
        a.run("p", "m", tmp_path)  # a folder, not a file
    assert started == []


def test_a_command_that_cannot_start_leaves_its_transcript_closed(tmp_path):
    a = agent.Agent(cfg(tmp_path), command=[str(tmp_path / "missing.exe")])
    a.disallowed = []
    transcript = tmp_path / "t.jsonl"
    with pytest.raises(GleanError, match="can't start Claude Code"):
        a.run("p", "m", transcript)
    transcript.unlink()  # closed: Windows refuses to delete an open file


def test_a_transcript_that_fails_mid_run_stops_the_run(tmp_path, fake, monkeypatch):
    fake("linger")
    a = make(tmp_path)
    a.prepare()
    started = []
    real = a.popen
    a.popen = lambda *args, **kwargs: started.append(real(*args, **kwargs)) or started[-1]

    class Full:
        def write(self, data):
            raise OSError(28, "No space left on device")

        def close(self):
            pass

    real_open = agent.Path.open
    monkeypatch.setattr(
        agent.Path, "open", lambda self, mode="r", *a, **k: Full() if mode == "ab" else real_open(self, mode, *a, **k)
    )
    with pytest.raises(OSError, match="No space left"):
        a.run("p", "m", tmp_path / "t.jsonl")
    (proc,) = started
    assert proc.poll() is not None  # ended, not left running
    assert proc.stdout.closed


def test_a_run_ends_with_a_structured_answer(tmp_path, fake):
    starts = fake()
    result = make(tmp_path).run("p", "m")
    assert (result.verdict, result.summary, result.report) == (
        "imported",
        "a reply about the offer",
        "**Interaction**: int_1",
    )
    args = starts()[-1]["args"]
    schema = json.loads(args[args.index("--json-schema") + 1])
    assert schema["properties"]["outcome"]["enum"] == list(agent.VERDICTS)
    assert "StructuredOutput" in args[args.index("--allowedTools") + 1].split(",")


@pytest.mark.parametrize("mode, verdict", [("skip", "skipped"), ("spam", "spam")])
def test_the_agent_may_judge_an_item_not_worth_importing(tmp_path, fake, mode, verdict):
    fake(mode, reason="a price alert")
    result = make(tmp_path).run("p", "m")
    assert (result.ok, result.verdict, result.summary) == (True, verdict, "a price alert")


def test_a_result_without_its_structured_answer_is_taken_as_imported(tmp_path, fake):
    fake("unstructured", report="Imported, in words.")
    result = make(tmp_path).run("p", "m")
    assert (result.ok, result.verdict, result.report) == (True, None, "Imported, in words.")


def test_stop_ends_every_run_in_progress(tmp_path, fake):
    fake("hang")
    a = make(tmp_path)
    a.prepare()
    results = []
    worker = threading.Thread(target=lambda: results.append(a.run("p", "m")))
    worker.start()
    deadline = time.monotonic() + 20
    while not a.live and time.monotonic() < deadline:
        time.sleep(0.05)
    a.stop()
    worker.join(timeout=20)
    assert results[0].outcome == "crashed"  # ended without a result
    assert not a.live
    a.stop()  # nothing running: nothing to do
