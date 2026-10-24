"""The `glean` command, end to end: set up, download, list, run, show, ignore, reset, status and doctor, with a folder
of .eml files as the mailbox and a fake Claude Code as the agent."""

import io
import json
import runpy
import subprocess
import sys
import urllib.error

import pytest

from glean import agent, cli, settings
from glean.connectors.mail import oauth

from .mail import samples
from .test_agent import FAKE
from .test_sift import FakeSift


def run(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def mail_home(home, monkeypatch, tmp_path):
    """A home set up for mail from a folder of .eml files, and the fake Claude Code as the agent."""
    eml = tmp_path / "eml"
    (eml / "Sent").mkdir(parents=True)
    (eml / "a.eml").write_bytes(samples.plain())
    (eml / "b.eml").write_bytes(samples.html_only())
    (eml / "c.eml").write_bytes(samples.with_attachments())
    (eml / "Sent" / "d.eml").write_bytes(samples.plain(message_id="<mine@x>", sender="john@example.com",
                                                       date="Thu, 15 Oct 2026 18:00:00 +0200"))  # fmt: skip
    home.mkdir()
    (home / "glean.toml").write_text(
        f'[mail]\naddress = "john@example.com"\nskip_senders = ["news@*"]\neml_folder = {json.dumps(str(eml))}\n',
        encoding="utf-8",
    )
    real = agent.Agent
    monkeypatch.setattr(cli.agent, "Agent", lambda cfg, **kw: real(cfg, command=[sys.executable, FAKE], **kw))
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "ok"}))
    return home


def test_help_names_every_command(capsys):
    code, out, _ = run(capsys)
    assert code == 0
    for command in ("setup", "status", "doctor", "mail"):
        assert command in out
    code, out, _ = run(capsys, "mail", "--help")
    assert code == 0
    for action in ("download", "list", "run", "show", "ignore", "reset", "login", "logout"):
        assert action in out


def test_unknown_arguments_fail(capsys):
    assert run(capsys, "status", "--colour")[0] == 2
    assert run(capsys, "mail", "list", "--latest", "0")[0] == 2


def test_the_whole_flow(mail_home, capsys):
    code, out, err = run(capsys, "mail", "download")
    assert code == 0
    assert out == "4 new emails (3 in Inbox, 1 in Sent), 1 of them skipped by your skip_senders; 0 known already\n"
    assert "Inbox: downloading 3 emails" in err

    code, out, _ = run(capsys, "mail", "list")
    lines = out.splitlines()
    assert len(lines) == 3  # the newsletter is skipped
    assert "Offerte Wartungsvertrag 2027" in lines[0]
    assert "<- Hans Müller <hans.mueller@acm…" in lines[0]
    assert "-> john@example.com" in lines[2]
    code, out, _ = run(capsys, "mail", "list", "--include-skipped", "--json")
    assert [i["title"] for i in json.loads(out)][-1] == "Newsletter"

    code, out, err = run(capsys, "mail", "run", "--latest", "2")
    assert code == 0
    assert out.startswith("done    ")
    assert len(out.splitlines()) == 2
    assert "2 done" in err
    assert err.index("reviewing 2 imports with opus") < err.index("2 done")  # reviewed in the run, by a stronger model
    assert "reviewed 2 imports ($0.12): 0 to import again;" in err
    code, out, _ = run(capsys, "mail", "list", "--status", "done")
    first = out.splitlines()[-1].split()[0]

    code, out, _ = run(capsys, "mail", "show", first)
    assert "status:     done" in out
    assert "**Interaction**: int_1" in out
    assert "run 2026" in out

    code, out, _ = run(capsys, "mail", "ignore", "<CAF1234@mail.acme.example>")
    assert out.startswith("ignored ")
    code, out, _ = run(capsys, "mail", "reset", "caf1234@mail.acme.example")
    assert out.startswith("new     ")
    listed = json.loads(run(capsys, "mail", "list", "--json")[1])
    assert [i["wanted"] for i in listed] == ["the owner"]  # imported without a first look

    code, out, _ = run(capsys, "status")
    assert "4 emails: 1 new, 2 done, 1 skipped" in out
    code, out, _ = run(capsys, "status", "--json")
    status = json.loads(out)
    assert sum(status["sources"]["mail"]["items"].values()) == 4
    assert status["sources"]["mail"]["cost_usd"] == pytest.approx(
        0.36
    )  # a first look and an import, the owner's no look


def test_a_download_takes_one_count_above_zero(mail_home, capsys):
    assert run(capsys, "mail", "download", "--oldest", "0")[0] == 2
    assert run(capsys, "mail", "download", "--oldest", "1", "--latest", "1")[0] == 2


def test_a_download_of_some_says_how_many_are_left(mail_home, capsys):
    code, out, _ = run(capsys, "mail", "download", "--latest", "3")
    assert code == 0
    assert out.startswith("3 new emails")
    assert out.endswith("; 0 known already; 1 more to download\n")
    code, out, _ = run(capsys, "mail", "download", "--oldest", "5")
    assert out.endswith("; 0 known already\n")


def test_each_download_run_and_review_keeps_a_log(mail_home, home, capsys, monkeypatch):
    run(capsys, "mail", "download")
    run(capsys, "mail", "run", "--latest", "1")
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "offline"}))
    run(capsys, "mail", "run", "--latest", "1")
    found = sorted((home / "logs").glob("*.log"))
    assert [p.name.split("-20")[0] for p in found] == ["mail-download", "mail-run", "mail-run"]
    assert "INFO 4 new emails (3 in Inbox, 1 in Sent)" in found[0].read_text(encoding="utf-8")
    runs = " ".join(p.read_text(encoding="utf-8") for p in found[1:])
    assert "INFO done " in runs  # the item's line, which goes to stdout
    assert "INFO reviewing 1 import with opus" in runs
    assert "ERROR GleanError: sift isn't connected" in runs
    assert "list" not in " ".join(p.name for p in found)  # reading commands keep none


def test_a_dry_run(mail_home, capsys):
    run(capsys, "mail", "download")
    code, out, _ = run(capsys, "mail", "run", "--latest", "1", "--dry-run")
    assert code == 0
    assert "Here is the next email to record in orbit and sift." in out  # the owner's own: no first look
    assert run(capsys, "mail", "list")[1].count("\n") == 3  # still waiting


def test_a_run_without_its_review(mail_home, capsys):
    run(capsys, "mail", "download")
    code, _, err = run(capsys, "mail", "run", "--oldest", "1", "--no-review")
    assert code == 0
    assert "review" not in err


def test_a_review_of_the_latest_imports_when_asked(mail_home, capsys):
    run(capsys, "mail", "download")
    assert run(capsys, "mail", "review")[2].strip() == "nothing imported yet to review"
    run(capsys, "mail", "run", "--oldest", "3", "--no-review")
    code, _, err = run(capsys, "mail", "review", "--latest", "2")
    assert code == 0
    assert "reviewing 2 imports with opus" in err
    first = json.loads(run(capsys, "mail", "list", "--status", "done", "--json")[1])[0]["id"]
    assert "reviewing 1 import with opus" in run(capsys, "mail", "review", first)[2]


def test_run_needs_to_know_how_many(mail_home, capsys):
    code, _, err = run(capsys, "mail", "run")
    assert code == 1
    assert "--oldest N (as they came), --latest N, or their ids" in err


def test_the_oldest_or_the_newest(mail_home, capsys):
    run(capsys, "mail", "download")
    waiting = [i["id"] for i in json.loads(run(capsys, "mail", "list", "--json")[1])]  # oldest first
    assert [line.split()[0] for line in run(capsys, "mail", "list", "--oldest", "2")[1].splitlines()] == waiting[:2]
    assert [line.split()[0] for line in run(capsys, "mail", "list", "--latest", "1")[1].splitlines()] == waiting[-1:]
    oldest = run(capsys, "mail", "run", "--oldest", "1", "--dry-run")[1]
    assert waiting[0] in oldest
    assert waiting[-1] not in oldest
    assert waiting[-1] in run(capsys, "mail", "run", "--latest", "1", "--dry-run")[1]


def test_the_oldest_and_the_newest_at_once_is_refused(mail_home, capsys):
    assert run(capsys, "mail", "run", "--oldest", "1", "--latest", "1")[0] == 2


def test_nothing_to_list_or_run(mail_home, capsys):
    assert "no emails to show" in run(capsys, "mail", "list")[2]
    assert "no emails waiting" in run(capsys, "mail", "run", "--latest", "3")[2]


def test_a_failed_run_exits_with_1(mail_home, capsys, monkeypatch):
    run(capsys, "mail", "download")
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "max_turns"}))
    code, out, _ = run(capsys, "mail", "run", "--latest", "1")
    assert code == 1
    assert out.startswith("failed  ")


def test_the_agent_check_failing_stops_the_run(mail_home, capsys, monkeypatch):
    run(capsys, "mail", "download")
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "offline"}))
    code, _, err = run(capsys, "mail", "run", "--latest", "1")
    assert code == 1
    assert "sift isn't connected" in err


def test_an_unknown_id(mail_home, capsys):
    code, _, err = run(capsys, "mail", "show", "nope")
    assert code == 1
    assert "glean: no mail item with the id or key nope" in err


def test_without_setup_and_not_in_a_terminal(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code, _, err = run(capsys, "status")
    assert code == 1
    assert "glean setup" in err


def test_first_use_in_a_terminal_starts_the_setup(mail_home, capsys, monkeypatch):
    (mail_home / "glean.toml").unlink()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    called = []
    monkeypatch.setattr(cli.wizard, "run", lambda h: called.append(h) or (h / "glean.toml").write_text("[agent]\n"))
    code, _, err = run(capsys, "status")
    assert code == 0
    assert called == [settings.current().home]
    assert "starting the setup" in err


def test_setup_command(capsys, monkeypatch):
    called = []
    monkeypatch.setattr(cli.wizard, "run", called.append)
    assert run(capsys, "setup")[0] == 0
    assert called == [settings.current().home]


def test_login_command_reaches_the_connector(mail_home, capsys, monkeypatch):
    class App:
        def get_accounts(self, username=None):
            return [{"username": "john@example.com"}]

        def remove_account(self, account):
            pass

    monkeypatch.setattr(oauth.msal, "PublicClientApplication", lambda *a, **k: App())
    text = (mail_home / "glean.toml").read_text(encoding="utf-8").replace("eml_folder", 'client_id = "cid"\n#')
    (mail_home / "glean.toml").write_text(text, encoding="utf-8")
    code, out, _ = run(capsys, "mail", "logout")
    assert (code, out) == (0, "signed out: tokens of 1 account removed\n")


def test_ctrl_c(capsys, monkeypatch):
    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "status_command", interrupted)
    assert run(capsys, "status")[0] == 130


class Completed:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


@pytest.fixture
def doctor(mail_home, monkeypatch, tmp_path):
    """Claude Code and the servers as `glean doctor` sees them; change the dict to break one."""
    world = {"version": "2.1.303 (Claude Code)", "auth": 0, "down": set()}
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(cli.agent, "find_claude", lambda configured=None: exe)

    def fake_run(cmd, **kwargs):
        return Completed(world["version"]) if cmd[1] == "--version" else Completed("{}", world["auth"])

    def fake_urlopen(url, timeout):
        if any(name in url for name in world["down"]):
            raise urllib.error.URLError("refused")
        raise urllib.error.HTTPError(url, 405, "Method Not Allowed", {}, io.BytesIO())

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    collections = [{"name": "data", "path": str(mail_home)}, {"name": "crm", "path": str(tmp_path / "vault")}]
    monkeypatch.setattr(cli.sift.Sift, "find", classmethod(lambda cls: cli.sift.Sift(["sift"], FakeSift(collections))))
    monkeypatch.setattr(cli.sift.orbit, "vault", lambda url, timeout=None: tmp_path / "vault")
    return world


def test_doctor_when_all_is_well(doctor, capsys):
    code, out, _ = run(capsys, "doctor")
    assert code == 0, out
    lines = out.splitlines()
    assert all(line.startswith("ok  ") for line in lines)
    assert any("first look: no tools but its answer, no servers" in line for line in lines)
    assert any("import: 18 tools, orbit and sift connected, nothing else" in line for line in lines)
    assert any("review: 21 tools on orbit's /mcp, merges included" in line for line in lines)
    assert any("mail: reads .eml files from" in line for line in lines)
    assert any("sift: the collection 'data' holds the mail archive" in line for line in lines)
    assert any("sift: the collection 'crm' holds orbit's vault" in line for line in lines)


def test_doctor_names_what_is_wrong(doctor, capsys):
    doctor["version"] = "2.1.289 (Claude Code)"
    doctor["auth"] = 1
    doctor["down"] = {"8765"}
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert "needs 2.1.290+" in out
    assert "!!  claude: not signed in" in out
    assert "!!  sift: nothing answers at http://127.0.0.1:8765/mcp" in out
    assert "import:" not in out  # not tried while something else is wrong


def test_doctor_with_the_agent_check_failing(doctor, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "missing"}))
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert "!!  import: the servers lack tools" in out


def test_doctor_names_an_orbit_too_old_for_the_review(doctor, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "old_orbit"}))  # no retract_observation: before 0.10
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert "!!  review: the servers lack tools glean needs: mcp__orbit__retract_observation; update orbit" in out


def test_doctor_without_a_review_checks_none(doctor, capsys, mail_home):
    text = (mail_home / "glean.toml").read_text(encoding="utf-8")
    (mail_home / "glean.toml").write_text("[agent]\nreview_every = 0\n" + text, encoding="utf-8")
    assert "review:" not in run(capsys, "doctor")[1]


def test_doctor_without_claude(mail_home, capsys, monkeypatch):
    def missing(configured=None):
        raise cli.GleanError("Claude Code isn't installed")

    monkeypatch.setattr(cli.agent, "find_claude", missing)
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"ok"))
    code, out, _ = run(capsys, "doctor")
    assert code == 1
    assert "!!  claude: Claude Code isn't installed" in out
    assert "ok  orbit: answers at" in out


def test_doctor_when_claude_hangs(doctor, capsys, monkeypatch):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(cli.subprocess, "run", hang)
    assert "!!  claude:" in run(capsys, "doctor")[1]


def test_main_module(mail_home):
    done = subprocess.run([sys.executable, "-m", "glean", "--version"], capture_output=True, text=True, timeout=60)
    assert done.stdout.startswith("glean ")


def test_show_without_its_report_file(mail_home, capsys):
    run(capsys, "mail", "download")
    run(capsys, "mail", "run", "--latest", "1")
    first = run(capsys, "mail", "list", "--status", "done")[1].split()[0]
    for report in (mail_home / "runs").rglob("report.md"):
        report.unlink()
    code, out, _ = run(capsys, "mail", "show", first)
    assert code == 0
    assert "report (" not in out


def test_running_the_package(mail_home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["glean", "status"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("glean", run_name="__main__")
    assert stopped.value.code == 0
    assert "home:" in capsys.readouterr().out


def test_a_damaged_state_database_is_named(mail_home, capsys):
    (mail_home / "state.db").write_bytes(b"this is no database" * 100)
    code, _, err = run(capsys, "mail", "list")
    assert code == 1
    assert err.startswith("glean: ")
    assert "state.db" in err
    assert "Traceback" not in err


def test_output_to_a_stream_that_cannot_show_every_character(mail_home, capsys, monkeypatch):
    eml = mail_home.parent / "eml"
    (eml / "e.eml").write_bytes(samples.plain(message_id="<cjk@x>", subject="会議の件 \u2013 Termin"))
    run(capsys, "mail", "download")
    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", narrow)
    assert cli.main(["mail", "list"]) == 0
    assert cli.main(["mail", "list", "--json"]) == 0
    narrow.flush()
    written = narrow.buffer.getvalue().decode("cp1252")
    assert "???? \u2013 Termin" in written  # the dash is in cp1252; the Japanese characters are not
    assert "\\u4f1a" in written  # JSON keeps every character, escaped


def test_rows_of_items_dated_before_1970(mail_home, capsys):
    eml = mail_home.parent / "eml"
    (eml / "old.eml").write_bytes(samples.plain(message_id="<old@x>", date="Mon, 01 Jan 1900 00:00:00 +0000"))
    assert run(capsys, "mail", "download")[0] == 0
    code, out, _ = run(capsys, "mail", "list", "--status", "new", "--latest", "50")
    assert code == 0
    assert "1900-01-01" in out


def test_output_to_a_stream_that_cannot_be_reconfigured(monkeypatch):
    plain = io.StringIO()
    monkeypatch.setattr(sys, "stdout", plain)
    assert cli.main([]) == 0
    assert "usage: glean" in plain.getvalue()


def test_download_keeps_sift_in_line(mail_home, capsys, monkeypatch, tmp_path):
    fake = FakeSift([{"name": "arxiv", "path": str(tmp_path / "papers")}])
    monkeypatch.setattr(cli.sift.Sift, "find", classmethod(lambda cls: cli.sift.Sift(["sift"], fake)))
    code, _, err = run(capsys, "mail", "download")
    assert code == 0
    archive = mail_home / "mails"
    assert f"sift: added the collection 'mails' for {archive}" in err
    assert "sift: indexing mails, which takes a while the first time: scans are read with OCR" in err
    assert "sift: mails: 3 files, 3 added, 0 changed, 0 removed (0.1s)" in err
    assert "message.eml" in (archive / ".siftignore").read_text(encoding="utf-8")
    assert fake.calls[-1] == ["index", "mails"]


def test_a_run_has_sift_index_the_archive_first_which_an_interrupted_download_may_not_have(
    mail_home, capsys, monkeypatch, tmp_path
):
    run(capsys, "mail", "download")  # no sift on PATH: as if the download had stopped before indexing
    fake = FakeSift([{"name": "mails", "path": str(mail_home / "mails")}])
    monkeypatch.setattr(cli.sift.Sift, "find", classmethod(lambda cls: cli.sift.Sift(["sift"], fake)))
    assert run(capsys, "mail", "run", "--latest", "1", "--no-review")[0] == 0
    assert ["index", "mails"] in fake.calls


def test_a_run_tells_the_agent_the_collections_that_hold_its_files(mail_home, capsys, monkeypatch, tmp_path):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE", json.dumps({"mode": "ok", "log": str(log)}))
    collections = [{"name": "data", "path": str(mail_home)}, {"name": "vault", "path": str(tmp_path / "v")}]
    monkeypatch.setattr(cli.sift.Sift, "find", classmethod(lambda cls: cli.sift.Sift(["sift"], FakeSift(collections))))
    monkeypatch.setattr(cli.sift.orbit, "vault", lambda url, timeout=None: tmp_path / "v")
    run(capsys, "mail", "download")
    assert run(capsys, "mail", "run", "--latest", "1", "--no-review")[0] == 0
    prompt = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["prompt"]
    assert 'sift collection "data"' in prompt
    assert 'the collection "vault"' in prompt


def test_doctor_on_orbit_s_full_endpoint(doctor, mail_home, capsys):
    text = (mail_home / "glean.toml").read_text(encoding="utf-8")
    (mail_home / "glean.toml").write_text('[orbit]\nurl = "http://127.0.0.1:8775/mcp"\n' + text, encoding="utf-8")
    out = run(capsys, "doctor")[1]
    assert "ok  orbit: /mcp serves merges and renames too, which glean hides; /mcp/write leaves them out" in out


def test_list_brings_older_mail_in_line_unless_glean_is_at_work(mail_home, capsys):
    run(capsys, "mail", "download")
    with cli.pipeline.lock(mail_home):
        assert run(capsys, "mail", "list")[0] == 0  # another glean at work: list just lists
