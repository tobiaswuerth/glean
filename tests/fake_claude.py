"""A fake `claude -p` for the tests: it logs how it was started, then plays the events of a run, one answer for each
message glean sends (stream-json input, one event per line). FAKE_CLAUDE (JSON) picks the run: {"mode": ..., "log":
path}; the messages after the first are appended to `log` + ".followups"."""

import io
import json
import os
import re
import sys
import time
from pathlib import Path

ALLOWED = [
    *(
        f"mcp__orbit__{t}"
        for t in (
            "resolve_entity get_entity_context search_entities list_entities log_interaction upsert_person "
            "upsert_organization upsert_topic link_entities link_document add_observation set_fields create_task "
            "update_task propose_review"
        ).split()
    ),
    "mcp__sift__query",
    "mcp__sift__search",
    "mcp__sift__reindex",
]
OTHERS = [
    "mcp__orbit__merge_entities",
    "mcp__orbit__rename_entity",
    "mcp__orbit__link_by_domain",
    "mcp__orbit__lint_vault",
    "mcp__orbit__settle_review",
    "mcp__sift__vsearch",
]


def messages():
    """The messages glean sends: the content of each user event on stdin, in UTF-8 as Claude Code reads it,
    whatever the locale's encoding."""
    for line in io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8"):
        if line.strip():
            yield json.loads(line)["message"]["content"]


def main():
    spec = json.loads(os.environ.get("FAKE_CLAUDE", "{}"))
    mode = spec.get("mode", "ok")
    args = sys.argv[1:]
    incoming = messages()
    first = next(incoming, "")
    option = {a: args[i + 1] for i, a in enumerate(args[:-1]) if a.startswith("--")}
    if spec.get("log"):
        record = {
            "args": args,
            "cwd": os.getcwd(),
            "cwd_files": os.listdir(),
            "stdin": first,
            "mcp": json.loads(Path(option["--mcp-config"]).read_text(encoding="utf-8")),
            "prompt": Path(option["--system-prompt-file"]).read_text(encoding="utf-8"),
        }
        with open(spec["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    if mode == "crash":
        sys.stderr.write("boom: something broke\n")
        sys.exit(3)
    if mode == "silent":
        sys.exit(0)
    hidden = set(option.get("--disallowedTools", "").split(","))
    tools = [t for t in ALLOWED + OTHERS if t not in hidden]
    if "--json-schema" in option:
        tools = ["StructuredOutput", *tools]
    if mode == "builtin":
        tools = ["Bash", *tools]
    if mode == "missing":
        tools = [t for t in tools if t != "mcp__orbit__propose_review"]
    if mode == "old_orbit":
        tools = [t for t in tools if t != "mcp__orbit__settle_review"]
    servers = [{"name": "orbit", "status": "connected"}, {"name": "sift", "status": "connected"}]
    if mode == "offline":
        servers[1]["status"] = "failed"
    emit({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": servers, "model": "claude-sonnet-5-5"})
    if mode == "hang":
        time.sleep(60)
    answer(spec | {"first": first}, mode, 0)
    for n, followup in enumerate(incoming, 1):
        if spec.get("log"):
            with open(spec["log"] + ".followups", "a", encoding="utf-8") as f:
                f.write(json.dumps(followup) + "\n")
        answer(spec | {"first": first}, mode, n)
    if mode == "linger":
        time.sleep(60)


def answer(spec, mode, n):
    """Play the run's answer to its n-th message."""
    if mode == "garbage":
        print("this is not JSON", flush=True)
        print("", flush=True)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Working on it."}]}})
    if mode == "max_turns":
        error = "Reached maximum number of turns (60)"
        emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 60,
              "total_cost_usd": 0.9, "duration_ms": 1000, "errors": [error]})  # fmt: skip
        return
    if mode == "policy":
        text = (
            "API Error: Haiku can't help with this. Start a new session to continue.\n\nLearn more: "
            "https://www.anthropic.com/legal/aup\n\nDetails: `[x]`"
        )
        emit({"type": "result", "subtype": "success", "is_error": True, "result": text, "num_turns": 2,
              "total_cost_usd": 0, "duration_ms": 5, "terminal_reason": "api_error"})  # fmt: skip
        return
    if mode == "error_text":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "API Error: overloaded",
              "num_turns": 1, "total_cost_usd": 0, "duration_ms": 5})  # fmt: skip
        return
    if mode == "review":
        given = {"report": "Merged two Hans.", "reimport": ["e1"], "suggestions": ["Say who signs."]}
        emit({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(given), "num_turns": 30,
              "total_cost_usd": 1.5, "duration_ms": 60000, "structured_output": given})  # fmt: skip
        return
    verdict = {"skip": "skipped", "spam": "spam"}.get(mode, "imported")
    if verdict == "imported" and not (mode == "stubborn" or (mode == "sloppy" and n == 0)):
        work(spec.get("first", ""))
    report = spec.get("report", "**Interaction**: int_1")
    denied = [{"tool_name": "mcp__orbit__merge_entities", "tool_input": {}}] if mode == "denied" else []
    given = {"outcome": verdict, "reason": spec.get("reason", "a reply about the offer"), "report": report}
    event = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(given), "num_turns": 7,
             "total_cost_usd": round(0.12 * (n + 1), 2), "duration_ms": 4321, "permission_denials": denied}  # fmt: skip
    if mode != "unstructured" and not (mode == "unstructured_once" and n == 0):
        event["structured_output"] = given
    else:
        event["result"] = report
    emit(event)


def work(item):
    """The calls an import makes, as glean's checks look for them: the interaction logged with the item's addresses,
    sift asked to index."""
    logged = {"kind": "email", "to": re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", item)}
    calls = [("mcp__orbit__log_interaction", logged, '{"id": "int_1"}'), ("mcp__sift__reindex", {}, "[]")]
    for n, (name, given, result) in enumerate(calls):
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"c{n}", "name": name,
                                                              "input": given}]}})  # fmt: skip
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"c{n}",
                                                         "content": result}]}})  # fmt: skip


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
