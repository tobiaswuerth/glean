"""What glean checks of an import before it counts as done, from the run's own tool calls as Claude Code reported
them: the interaction logged; every address, name and organization orbit didn't know resolved or explained; sift asked
to index; no failed call dropped without a word. The agent gets the problems back, in the same session, to fix."""

import json
from dataclasses import dataclass

ORBIT, SIFT = "mcp__orbit__", "mcp__sift__"
UNKNOWN = ("unknown_addresses", "unknown_people", "unknown_orgs", "unknown_topics")  # what log_interaction lists


@dataclass(frozen=True)
class Call:
    name: str
    given: dict
    ok: bool
    answer: str  # the tool's result, as text


def calls(events):
    """The tool calls of a run's events, in order, each with its result."""
    asked, made = {}, []
    for event in events:
        for part in (event.get("message") or {}).get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                asked[part.get("id")] = (part.get("name") or "", part.get("input") or {})
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in asked:
                name, given = asked.pop(part["tool_use_id"])
                made.append(Call(name, given, not part.get("is_error"), _text(part.get("content"))))
    return made


def problems(events, answer, addresses=()):
    """What an import the agent answered `imported` leaves undone, as instructions to it; none for another answer.
    `addresses`: the item's sender and recipients, which the interaction must name."""
    if not isinstance(answer, dict) or answer.get("outcome") != "imported":
        return []
    made = calls(events)
    logged = [i for i, c in enumerate(made) if c.name == ORBIT + "log_interaction" and c.ok]
    if not logged:
        return ["log_interaction never succeeded: log the email first, then write everything with its id as source"]
    report = str(answer.get("report") or "")
    named = _said(made[logged[0] :], report)
    found = [
        f"{address} is in the email's From, To or Cc, but not in log_interaction: resolve and link it with the "
        "interaction, or say in the report why not"
        for address in addresses
        if address.casefold() not in named
    ]
    later = _said(made[logged[0] + 1 :], report)
    for kind, item in _unknown(made[logged[-1]].answer):
        if item.casefold() not in later:
            found.append(f"{item}, which log_interaction listed under {kind}: resolve and link it, or say in the "
                         "report why not")  # fmt: skip
    if not any(c.name == SIFT + "reindex" for c in made):
        found.append("sift reindex wasn't called: call it once, with the Markdown file and the paths orbit changed")
    for i, c in enumerate(made):
        if c.ok or not c.name.startswith(ORBIT) or c.name.removeprefix(ORBIT) in report:
            continue
        if not any(d.name == c.name and d.ok for d in made[i + 1 :]):
            found.append(f"{c.name.removeprefix(ORBIT)} failed ({' '.join(c.answer.split())[:200]}) and wasn't done "
                         "since: fix and call it again, or say in the report why not")  # fmt: skip
    return found


def _said(made, report):
    """What the successful calls were given, and the report, as one text to look things up in, case aside."""
    return (" ".join(json.dumps(c.given, ensure_ascii=False) for c in made if c.ok) + " " + report).casefold()


def _unknown(answer):
    """(kind, item) for what log_interaction's answer lists as unknown."""
    try:
        found = json.loads(answer)
    except ValueError:
        return []
    if not isinstance(found, dict):
        return []
    return [(kind, str(item)) for kind in UNKNOWN for item in (found.get(kind) or []) if str(item).strip()]


def _text(content):
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")
