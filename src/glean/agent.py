"""One agent run per item: a new `claude -p` process with a fresh context, whose only tools are orbit's reading and
writing tools and sift's search and reindex. The run is sandboxed by Claude Code's own flags, and checked: each run's
first event lists the tools and servers it really has, and a run with anything else is stopped before it acts."""

import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from . import checks
from .errors import GleanError

ORBIT_TOOLS = (
    "resolve_entity",
    "get_entity_context",
    "search_entities",
    "list_entities",
    "log_interaction",
    "upsert_person",
    "upsert_organization",
    "upsert_topic",
    "link_entities",
    "link_document",
    "add_observation",
    "set_fields",
    "create_task",
    "update_task",
    "propose_review",
)  # not merge_entities, rename_entity, create_missing_references or lint_vault: no destructive upkeep
SIFT_TOOLS = ("query", "search", "reindex")
ALLOWED = tuple(f"mcp__orbit__{t}" for t in ORBIT_TOOLS) + tuple(f"mcp__sift__{t}" for t in SIFT_TOOLS)
REVIEW_TOOLS = (
    *(t for t in ORBIT_TOOLS if t not in ("log_interaction", "link_document", "create_task")),
    "merge_entities",
    "rename_entity",
    "link_by_domain",
    "lint_vault",
    "settle_review",
)  # a review fixes what imports wrote and acts on the owner's answers; it records no new interactions or tasks
SERVERS = ("orbit", "sift")
ANSWER = "StructuredOutput"  # the tool Claude Code gives a run whose answer has a schema; it changes nothing
VERDICTS = ("imported", "already_imported", "skipped", "spam")
SCHEMA = {  # the run's answer
    "type": "object",
    "properties": {
        "outcome": {
            "type": "string",
            "enum": list(VERDICTS),
            "description": "imported; already_imported (orbit had it all); skipped (automated mail, nothing to "
            "record); spam (unsolicited, a scam or phishing)",
        },
        "reason": {"type": "string", "description": "Why, in one short line, e.g. 'a price alert from a shop'."},
        "report": {"type": "string", "description": "The report for the owner, in Markdown."},
    },
    "required": ["outcome", "reason", "report"],
}
REVIEW_SCHEMA = {  # a review's answer
    "type": "object",
    "properties": {
        "report": {"type": "string", "description": "What you checked, found and fixed, in Markdown, for the owner."},
        "reimport": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The ids of items to import again: skipped though a person wrote them, or imported wrongly.",
        },
        "questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The open questions you asked the owner with propose_review, as you asked them.",
        },
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Changes to the importer's instructions that would prevent what went wrong more than once.",
        },
    },
    "required": ["report", "reimport", "questions", "suggestions"],
}


@dataclass(frozen=True)
class Role:
    """A kind of run: the tools it may use, its answer, orbit's endpoint for it, its model, and its limits."""

    allowed: tuple[str, ...]
    schema: dict
    orbit: str  # orbit's endpoint: /mcp/write without upkeep, /mcp with it
    model: str = "model"  # the [agent] setting that names its model
    scale: int = 1  # its turns, budget and time, in [agent]'s limits per item


IMPORT = Role(ALLOWED, SCHEMA, "/mcp/write")
REVIEW = Role(
    (*(f"mcp__orbit__{t}" for t in REVIEW_TOOLS), "mcp__sift__query", "mcp__sift__search"),
    REVIEW_SCHEMA,
    "/mcp",
    model="review_model",
    scale=10,
)
MIN_VERSION = (2, 1, 290)  # --json-schema's answer kept when the connection drops after it; --permission-prompts
PROBE_SECONDS = 60  # how long the check before a batch may take to start
EXIT_SECONDS = 15  # how long a run that gave its result may take to exit
FOLLOWUPS = 2  # how often glean sends a run back with what its checks found left to do
POLICY = "anthropic.com/legal/aup"  # in the text of a result the model's usage policy refused: what it read, not glean
NO_ANSWER = f"no answer came: give it with the {ANSWER} tool, its outcome, reason and report"
FOLLOWUP = (
    "glean checked your import, and this is left to do:\n{problems}\n\nDo it with the tools, then give your answer "
    "again. For anything you leave, say in the report why."
)


@dataclass
class Result:
    outcome: str  # success, error_max_turns, error_max_budget_usd, error_during_execution, timeout, refused, crashed
    ok: bool
    report: str = ""
    reason: str | None = None
    cost_usd: float = 0.0
    turns: int = 0
    duration_ms: int = 0
    denied: list = field(default_factory=list)  # tool calls the sandbox refused
    verdict: str | None = None  # the agent's answer: one of VERDICTS; None if it gave none
    summary: str | None = None  # its reason for it, in a line
    checks: list = field(default_factory=list)  # what glean's checks found left to do after the last answer
    sent_back: list = field(default_factory=list)  # each time glean sent the run back: what its checks found
    answer: dict = field(default_factory=dict)  # the structured answer as given

    @property
    def followups(self):
        return len(self.sent_back)


def find_claude(configured=None):
    """The claude executable: the configured one, else the native installer's, else the one on PATH."""
    if configured:
        if not Path(configured).is_file():
            raise GleanError(f"agent.claude in glean.toml is {configured}, which doesn't exist")
        found = Path(configured)
    else:
        native = Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
        which = shutil.which("claude")
        if not native.is_file() and not which:
            raise GleanError(
                "Claude Code isn't installed, or not on PATH: install it, or set agent.claude in glean.toml"
            )
        found = native if native.is_file() else Path(which)
    if found.suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise GleanError(
            f"{found} is a script wrapper, which would mangle glean's arguments: install the native Claude Code "
            "(claude.exe), or set agent.claude to it"
        )
    return found


def version(text):
    """The version in `claude --version`'s output, "2.1.303 (Claude Code)", as a tuple; () if there is none."""
    head = text.strip().split(" ")[0]
    parts = head.split(".")
    return tuple(int(p) for p in parts) if parts and all(p.isdigit() for p in parts) else ()


def endpoint(url, path):
    """orbit's URL for a role's endpoint (/mcp/write, /mcp): the configured one's, unless it is a URL of the owner's
    own that ends in none of orbit's endpoints."""
    for known in ("/mcp/write", "/mcp/read", "/mcp"):
        if url.endswith(known):
            return url.removesuffix(known) + path
    return url


class Agent:
    def __init__(self, cfg, command=None, popen=subprocess.Popen, role=IMPORT):
        self.cfg, self.popen, self.role = cfg, popen, role
        self.base = command or [str(find_claude(cfg.agent.claude))]  # tests run a fake claude here
        self.disallowed = None  # the servers' other tools, known after prepare()
        self.live, self.lock = set(), threading.Lock()  # the processes running, which stop() ends
        self.check = checks.problems if role is IMPORT else None  # what an answer leaves to do: it goes back
        self.orbit_url = endpoint(cfg.orbit_url, role.orbit)

    def stop(self):
        """End every run in progress, as Ctrl+C does with several at once: each then ends without a result."""
        with self.lock:
            running = list(self.live)
        for proc in running:
            with suppress(OSError):
                proc.kill()

    def mcp_config(self):
        return {
            "mcpServers": {
                "orbit": {"type": "http", "url": self.orbit_url},
                "sift": {"type": "http", "url": self.cfg.sift.url},
            }
        }

    def command(self, conf):
        a, scale = self.cfg.agent, self.role.scale
        cmd = [
            *self.base,
            "-p",
            "--input-format", "stream-json",  # messages as events: glean may send a run back
            "--tools", "",  # no built-in tools: no shell, no files, no web
            "--allowedTools", ",".join((*self.role.allowed, ANSWER)),
            "--permission-mode", "dontAsk",  # anything not allowed is denied, never asked
            "--permission-prompts", "none",
            "--restricted",  # no user, project or local settings: no CLAUDE.md, hooks or plugins of the owner's
            "--disable-slash-commands",  # no skills
            "--strict-mcp-config", "--mcp-config", str(conf / "mcp.json"),  # orbit and sift, no other servers
            "--system-prompt-file", str(conf / "prompt.md"),
            "--output-format", "stream-json", "--verbose",
            "--max-turns", str(a.max_turns * scale),
            "--model", getattr(a, self.role.model),
            "--no-session-persistence",
            "--json-schema", json.dumps(self.role.schema),  # the answer: for an import, outcome, reason, report
        ]  # fmt: skip
        if self.disallowed:
            cmd += ["--disallowedTools", ",".join(self.disallowed)]  # hidden from the model, not just refused
        if a.max_budget_usd is not None:
            cmd += ["--max-budget-usd", str(round(a.max_budget_usd * scale, 2))]
        if a.effort:
            cmd += ["--effort", a.effort]
        return cmd

    def prepare(self):
        """Check, before a batch, that Claude Code starts with both servers connected and every allowed tool there,
        and learn the servers' other tools, which every run then hides. Returns the tools a run will have."""
        events, _ = self._start("Reply with OK.", "This run only checks the setup.", None, until_init=True)
        init = events.get("init")
        if init is None:
            raise GleanError(f"Claude Code didn't start: {events.get('error') or 'no output'}")
        self._check_servers(init)
        tools = init.get("tools") or []
        if other := [t for t in tools if t != ANSWER and not t.startswith(("mcp__orbit__", "mcp__sift__"))]:
            raise GleanError(f"Claude Code offered tools glean doesn't allow: {', '.join(other)}; is it up to date?")
        if missing := [t for t in self.role.allowed if t not in tools]:
            raise GleanError(f"the servers lack tools glean needs: {', '.join(missing)}; update orbit and sift")
        self.disallowed = sorted(t for t in tools if t not in (*self.role.allowed, ANSWER))
        return [t for t in tools if t in self.role.allowed]

    def _check_servers(self, init):
        status = {s.get("name"): s.get("status") for s in init.get("mcp_servers") or []}
        urls = {"orbit": self.orbit_url, "sift": self.cfg.sift.url}
        for name in SERVERS:
            if status.get(name) != "connected":
                raise GleanError(
                    f"{name} isn't connected ({status.get(name) or 'missing'}): is `{name} serve` running at "
                    f"{urls[name]}?"
                )

    def run(self, system_prompt, message, transcript=None, addresses=()):
        """Run the agent on one item. `message` goes to it on stdin; every event is appended to `transcript`.
        `addresses`: the item's sender and recipients, which its interaction must name."""
        if self.disallowed is None:
            self.prepare()
        check = (lambda events, answer: self.check(events, answer, addresses)) if self.check else None
        _, result = self._start(system_prompt, message, transcript, check=check)
        return result

    def _start(self, system_prompt, message, transcript, until_init=False, check=None):
        with (
            tempfile.TemporaryDirectory(prefix="glean-conf-", ignore_cleanup_errors=True) as conf,
            tempfile.TemporaryDirectory(prefix="glean-run-", ignore_cleanup_errors=True) as work,
        ):
            conf = Path(conf)
            (conf / "mcp.json").write_text(json.dumps(self.mcp_config()), encoding="utf-8")
            (conf / "prompt.md").write_text(system_prompt, encoding="utf-8")
            timeout = PROBE_SECONDS if until_init else self.cfg.agent.timeout_minutes * 60 * self.role.scale
            return self._supervise(self.command(conf), Path(work), message, transcript, timeout, until_init, check)

    def _supervise(self, cmd, cwd, message, transcript, timeout, until_init, check=None):
        """Start the process in an empty folder, send it the message, and follow its events until its result, a
        timeout, or (until_init) its first event. An answer in which `check` finds things left to do goes back to the
        run, in the same session, as a message that lists them, at most FOLLOWUPS times. Returns ({"init": …,
        "error": …}, Result)."""
        log = Path(transcript).open("ab") if transcript else None  # noqa: SIM115 (closed below, after the loop)
        try:
            proc = self.popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            if log:
                log.close()
            raise GleanError(f"can't start Claude Code ({cmd[0]}): {e}") from None
        with self.lock:
            self.live.add(proc)
        lines, errors = queue.Queue(), []
        threads = [
            threading.Thread(target=_send, args=(proc.stdin, message), daemon=True),
            threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True),
            threading.Thread(target=lambda: errors.extend(proc.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        seen, result, events, earlier = {}, None, [], None
        try:
            while result is None:
                try:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise queue.Empty
                    line = lines.get(timeout=left)
                except queue.Empty:
                    result = Result("timeout", False, reason=f"no result within {timeout / 60:g} minutes")
                    break
                if line is None:  # the process ended without a result
                    proc.wait()
                    tail = b"".join(errors[-20:]).decode("utf-8", "replace").strip()
                    seen["error"] = tail or f"exit code {proc.returncode}"
                    result = Result("crashed", False, reason=f"Claude Code ended without a result: {seen['error']}")
                    break
                if log:
                    log.write(line if line.endswith(b"\n") else line + b"\n")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "system" and event.get("subtype") == "init":
                    seen["init"] = event
                    if until_init:
                        break
                    if refusal := self._refusal(event):
                        result = Result("refused", False, reason=refusal)
                elif event.get("type") == "result":
                    current = _merged(earlier, _result(event))
                    left = self._left(check, events, event, current) if check and current.ok else []
                    if left and current.followups < FOLLOWUPS:
                        current.sent_back.append(left)
                        message = FOLLOWUP.format(problems="\n".join(f"- {p}" for p in left))
                        if log:  # Claude Code doesn't echo what it reads: the transcript keeps glean's part
                            log.write(_line(message, glean="followup"))
                        threads.append(threading.Thread(target=_send, args=(proc.stdin, message), daemon=True))
                        threads[-1].start()
                        earlier = current
                        continue
                    current.checks = left
                    result = current
                else:
                    events.append(event)
            finished = not until_init and result is not None and result.outcome not in ("timeout", "refused")
            _stop(proc, threads, grace=EXIT_SECONDS if finished else 0)
        except BaseException:  # Ctrl+C, or the transcript can't be written: the run ends with glean's part in it
            _stop(proc, threads)
            raise
        finally:
            if log:
                log.close()
            with self.lock:
                self.live.discard(proc)
        return seen, result

    @staticmethod
    def _left(check, events, event, result):
        """What an answered run leaves to do: the answer itself, if it came without its schema, or what the checks
        find."""
        return check(events, event.get("structured_output")) if result.verdict else [NO_ANSWER]

    def _refusal(self, init):
        """Why a run must not go on, from its first event: other tools than allowed, or a server not connected."""
        tools, allowed = set(init.get("tools") or []) - {ANSWER}, set(self.role.allowed)
        if tools != allowed:
            extra, missing = sorted(tools - allowed), sorted(allowed - tools)
            return f"the run's tools weren't the allowed ones (also: {extra}; missing: {missing}), so it was stopped"
        try:
            self._check_servers(init)
        except GleanError as e:
            return str(e)
        return None


def _result(event):
    subtype = event.get("subtype") or "unknown"
    if event.get("is_error") and POLICY in str(event.get("result") or ""):
        subtype = "policy_refusal"
    ok = subtype == "success" and not event.get("is_error")
    answer = event.get("structured_output")
    answer = answer if isinstance(answer, dict) else {}
    report = answer.get("report") or event.get("result") or ""
    errors = event.get("errors") or []
    reason = None if ok else "; ".join(str(e) for e in errors) or report.strip()[:500] or subtype
    return Result(
        verdict=answer.get("outcome") if answer.get("outcome") in VERDICTS else None,
        summary=answer.get("reason"),
        outcome=subtype,
        ok=ok,
        report=report,
        reason=reason,
        cost_usd=float(event.get("total_cost_usd") or 0),
        turns=int(event.get("num_turns") or 0),
        duration_ms=int(event.get("duration_ms") or 0),
        denied=list(event.get("permission_denials") or []),
        answer=answer,
    )


def _merged(earlier, later):
    """A run's result over the answers it gave: the last answer, the turns and time of all; the cost Claude Code
    reports already counts the session's earlier answers."""
    if earlier is None:
        return later
    later.turns += earlier.turns
    later.duration_ms += earlier.duration_ms
    later.denied = earlier.denied + later.denied
    later.sent_back = earlier.sent_back
    return later


def _line(message, **extra):
    """A user message as a line of Claude Code's stream-json input."""
    event = {"type": "user", "message": {"role": "user", "content": message}, **extra}
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


def _send(stdin, message):
    """Send a message to a run; stdin stays open for more."""
    try:
        stdin.write(_line(message))
        stdin.flush()
    except OSError:  # the process ended or was stopped before reading it
        pass


def _pump(stdout, lines):
    for line in stdout:
        lines.put(line)
    lines.put(None)


def _stop(proc, threads, grace=0):
    """End the process: close its input, which ends a session, let it exit by itself for `grace` seconds, then kill
    it; then let the threads that read and write its pipes finish, and close the pipes."""
    with suppress(OSError):
        proc.stdin.close()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for thread in threads:
        thread.join(timeout=5)
    for pipe in (proc.stdout, proc.stderr):
        pipe.close()
