"""What glean checks of an import before it counts as done, from the run's tool calls."""

import json

from glean import checks

IMPORTED = {"outcome": "imported", "reason": "an offer", "report": "**Interaction**: int_1"}


def run(*calls):
    """Events of a run that made these calls: (tool, input, ok, answer)."""
    events = []
    for n, (name, given, ok, answer) in enumerate(calls):
        use = {"type": "tool_use", "id": f"t{n}", "name": name, "input": given}
        result = {"type": "tool_result", "tool_use_id": f"t{n}", "content": answer, "is_error": not ok}
        events += [
            {"type": "assistant", "message": {"content": [use]}},
            {"type": "user", "message": {"content": [result]}},
        ]
    return events


def logged(**unknown):
    return ("mcp__orbit__log_interaction", {"kind": "email"}, True, json.dumps({"id": "int_1", **unknown}))


REINDEX = ("mcp__sift__reindex", {"paths": ["x.md"]}, True, "[]")


def test_an_import_done_right_passes():
    assert checks.problems(run(logged(), REINDEX), IMPORTED) == []


def test_other_answers_are_not_checked():
    assert checks.problems([], {"outcome": "skipped"}) == []
    assert checks.problems([], None) == []


def test_an_import_without_its_interaction():
    (problem,) = checks.problems(run(REINDEX), IMPORTED)
    assert problem.startswith("log_interaction never succeeded")


def test_what_orbit_didnt_know_must_be_resolved_or_explained():
    unknown = logged(unknown_addresses=["lea@acme.example"], unknown_orgs=["Acme AG"])
    address, org = checks.problems(run(unknown, REINDEX), IMPORTED)
    assert address.startswith("lea@acme.example, which log_interaction listed under unknown_addresses")
    assert org.startswith("Acme AG, which log_interaction listed under unknown_orgs")
    resolved = ("mcp__orbit__upsert_person", {"fields": {"emails": ["Lea@acme.example"]}}, True, "{}")
    explained = IMPORTED | {"report": "Left out: Acme AG, only named in a signature"}
    assert checks.problems(run(unknown, resolved, REINDEX), explained) == []


def test_every_address_of_the_email_must_be_logged_or_explained():
    # An agent logged a class's email with only the owner in To, so orbit listed none of the 12 others as unknown.
    addresses = ("peter@school.example", "anna@school.example", "me@x.ch")
    logged_some = ("mcp__orbit__log_interaction", {"sender": "Peter <Peter@school.example>", "to": ["me@x.ch"]}, True,
                   json.dumps({"id": "int_1"}))  # fmt: skip
    (problem,) = checks.problems(run(logged_some, REINDEX), IMPORTED, addresses)
    assert problem.startswith("anna@school.example is in the email's From, To or Cc, but not in log_interaction")
    person = ("mcp__orbit__upsert_person", {"fields": {"emails": ["anna@school.example"]}}, True, "{}")
    assert checks.problems(run(logged_some, person, REINDEX), IMPORTED, addresses) == []
    explained = IMPORTED | {"report": "Left out: anna@school.example, a list address"}
    assert checks.problems(run(logged_some, REINDEX), explained, addresses) == []


def test_sift_must_be_asked_to_index():
    (problem,) = checks.problems(run(logged()), IMPORTED)
    assert problem.startswith("sift reindex wasn't called")


def test_a_failed_call_dropped_without_a_word():
    failed = ("mcp__orbit__create_task", {"title": "x"}, False, [{"type": "text", "text": "owner: no note has it"}])
    (problem,) = checks.problems(run(logged(), failed, REINDEX), IMPORTED)
    assert problem == (
        "create_task failed (owner: no note has it) and wasn't done since: fix and call it again, or say in the "
        "report why not"
    )
    again = ("mcp__orbit__create_task", {"title": "x", "owner": "Me"}, True, "{}")
    assert checks.problems(run(logged(), failed, again, REINDEX), IMPORTED) == []
    assert checks.problems(run(logged(), failed, REINDEX), IMPORTED | {"report": "create_task: no owner"}) == []
    refused = ("mcp__sift__reindex", {"paths": ["y"]}, False, "in no collection")
    assert checks.problems(run(logged(), refused), IMPORTED) == []  # sift's refusals are the report's to name


def test_an_answer_that_isnt_an_object_lists_nothing_unknown():
    for answer in ("not json", "[1, 2]"):
        assert checks.problems(run(("mcp__orbit__log_interaction", {}, True, answer), REINDEX), IMPORTED) == []


def test_parts_that_are_no_tool_calls_are_passed_over():
    events = [{"type": "assistant", "message": {"content": ["plain text", {"type": "text", "text": "x"}]}}]
    assert checks.calls(events + run(logged())) == checks.calls(run(logged()))
