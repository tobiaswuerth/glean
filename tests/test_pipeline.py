"""Choosing and importing items, the same for every source: shown with a minimal connector of notes."""

import dataclasses
import threading
import time

import pytest

from glean import agent, config, pipeline
from glean.errors import GleanError
from glean.state import State

from .conftest import NOW
from .notes_connector import Notes


class FakeAgent:
    def __init__(self, results=()):
        self.results, self.prepared, self.runs = list(results), 0, []

    def prepare(self):
        self.prepared += 1

    def run(self, prompt, message, transcript, addresses=()):
        self.runs.append((prompt, message, transcript))
        self.addresses = addresses
        transcript.write_text("{}\n", encoding="utf-8")
        result = self.results.pop(0) if self.results else agent.Result("success", True, "Imported.", None, 0.1, 5, 99)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "glean.toml"
    path.write_text('[sift]\ncrm_collection = "vault"\ndocument_collections = ["docs", "wiki"]\n', encoding="utf-8")
    cfg = config.load(path)
    notes = Notes(Notes.configure(config.Table({}, "notes", path)), tmp_path)
    notes.source = {
        "a": ("2026-10-01T09:00:00+02:00", "First", "one"),
        "b": ("2026-10-02T09:00:00+02:00", "Second", "two"),
        "c": ("2026-10-03T09:00:00+02:00", "Third", "three"),
    }
    with State(tmp_path, {"notes": notes.archive}, now=lambda: NOW) as state:
        notes.download(state)
        yield cfg, notes, state


def test_choose_the_oldest_first(setup):
    # The CRM grows as the mailbox did: people and tasks first, then what follows from them.
    _, notes, state = setup
    assert [i.key for i in pipeline.choose(state, notes, 2)] == ["a", "b"]
    assert [i.key for i in pipeline.choose(state, notes)] == ["a", "b", "c"]


def test_choose_the_newest_when_asked_still_oldest_first(setup):
    _, notes, state = setup
    assert [i.key for i in pipeline.choose(state, notes, 2, newest=True)] == ["b", "c"]


def test_choose_by_id_or_key(setup):
    _, notes, state = setup
    c = state.find("notes", "c")
    assert [i.key for i in pipeline.choose(state, notes, ids=[c.id, "a", c.id[:5]])] == ["a", "c"]


def test_choose_failed_and_skipped_only_when_asked(setup):
    _, notes, state = setup
    state.set_status(state.find("notes", "a"), "failed", "x")
    state.set_status(state.find("notes", "b"), "skipped", "bulk")
    state.set_status(state.find("notes", "c"), "ignored")
    assert pipeline.choose(state, notes, 10) == []
    assert [i.key for i in pipeline.choose(state, notes, 10, retry_failed=True)] == ["a"]
    assert [i.key for i in pipeline.choose(state, notes, 10, include_skipped=True)] == ["b"]


def test_the_system_prompt_has_the_sources_part(setup):
    cfg, notes, _ = setup
    prompt = pipeline.system_prompt(cfg, notes)
    assert "## Importing a note\n\nLog it." in prompt
    assert "You import one note" in prompt
    assert "in the collections 'docs', 'wiki'" in prompt
    assert 'sift collection "notes"' in prompt
    assert 'the collection "vault"' in prompt
    assert "{" not in prompt.replace('{"', "")  # every placeholder filled


def test_the_system_prompt_never_asks_whether_two_interactions_are_one(setup):
    # Agents asked whether a resent email and the first were the same, "so that one can be deleted".
    cfg, notes, _ = setup
    prompt = " ".join(pipeline.system_prompt(cfg, notes).split())
    assert "even one sent twice, or again with changes: never ask whether two interactions are the same" in prompt


def test_the_system_prompt_imports_automated_mail_that_settles_a_fact_about_the_owner(setup):
    # Registrations, admissions and services held came only as confirmations, which were all skipped.
    cfg, notes, _ = setup
    prompt = " ".join(pipeline.system_prompt(cfg, notes).split())
    assert "a company's answer to the owner's own request" in prompt
    assert "what a person wrote to people, however short or passing" in prompt  # not skipped as passing logistics
    assert "an automated note that settles a lasting fact about the owner" in prompt
    assert "These facts go on Me only" in prompt


def test_the_message_names_the_files_and_fences_the_item(setup):
    _, notes, state = setup
    item = state.find("notes", "a")
    doc = notes.document(item)
    text = pipeline.message(notes, item, doc)
    assert f"Its Markdown file (orbit's `source_file`, sift's document): {doc.markdown}" in text
    assert f"- Its original's sha256 (orbit's `sha256`): {item.sha256}" in text
    assert str(doc.original) not in text  # of no use to an agent without file tools, and sift doesn't index it
    assert "retry" not in text
    marker = text.split("between the lines <")[1].split(">")[0]
    assert text.endswith(f"<{marker}>\n# First\n\none\n</{marker}>\n")


def test_the_message_asks_for_a_decision_first(setup):
    # "Import this email" read as the owner's wish, and automated mail was imported "as asked".
    _, notes, state = setup
    item = state.find("notes", "a")
    first = pipeline.message(notes, item, notes.document(item)).split("\n")[0]
    assert first == (
        "Here is the next note. Decide by your instructions whether it is worth recording; if it is, import it into "
        "orbit and sift."
    )


def test_what_the_headers_say_is_evidence_not_a_verdict(setup):
    _, notes, state = setup
    item = state.find("notes", "a")
    doc = dataclasses.replace(notes.document(item), signals=("a List-Id header", "Precedence: bulk"))
    said = "- What its headers and sender say, as evidence, not a verdict: a List-Id header; Precedence: bulk\n"
    assert said in pipeline.message(notes, item, doc)
    assert "evidence" not in pipeline.message(notes, item, notes.document(item))


def test_a_retry_says_so(setup):
    _, notes, state = setup
    item = state.find("notes", "a")
    run = state.start_run(item)
    item = state.finish_run(run, item, status="failed", outcome="timeout", cost_usd=0, turns=1, duration_ms=1,
                            reason="no result within 5 minutes")  # fmt: skip
    text = pipeline.message(notes, item, notes.document(item), "timeout")
    assert "attempt 2; the last one ended with timeout (no result within 5 minutes)." in text
    item = state.reset(item)  # by hand, after a run
    assert "the last one ended with unknown." in pipeline.message(notes, item, notes.document(item))


def test_run_imports_oldest_first_and_records_each(setup):
    cfg, notes, state = setup
    out, said = [], []
    fake = FakeAgent([agent.Result("error_max_turns", False, "", "Reached maximum number of turns", 0.5, 60, 9)])
    counts = pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), said.append, out.append)
    assert counts == {"failed": 1, "done": 2}
    assert fake.prepared == 1
    assert all("\n<item-" in message for _, message, _ in fake.runs)
    a, b = state.find("notes", "a"), state.find("notes", "b")
    assert (a.status, a.reason, a.attempts, a.cost_usd) == ("failed", "Reached maximum number of turns", 1, 0.5)
    assert (b.status, b.attempts, b.document, b.report) == ("done", 1, f"{b.id}/Second.md", f"notes/{b.id}/report.md")
    report = (notes.home / "runs" / b.report).read_text(encoding="utf-8")
    assert '\nstatus: "done"\n' in report
    assert report.endswith("---\n\nImported.\n")
    assert (
        (notes.home / "runs" / a.report)
        .read_text(encoding="utf-8")
        .endswith("(no report: Reached maximum number of turns)\n")
    )
    assert out[0].startswith(f"failed  {a.id}  2026-10-01  First  (60 turns, $0.50: Reached")
    assert out[1] == f"done    {b.id}  2026-10-02  Second  (5 turns, $0.10)"
    assert said[0] == f"[1/3] {a.id}  2026-10-01  First"


def test_the_run_gets_the_addresses_its_interaction_must_name(setup, monkeypatch):
    cfg, notes, state = setup
    real = notes.document
    monkeypatch.setattr(notes, "document", lambda item: dataclasses.replace(real(item), addresses=("a@x.example",)))
    fake = FakeAgent()
    pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 1), print, print)
    assert fake.addresses == ("a@x.example",)


def test_refused_tool_calls_are_kept_in_the_report(setup):
    cfg, notes, state = setup
    denied = [{"tool_name": "mcp__orbit__merge_entities", "tool_input": {"keep": "x"}}]
    fake = FakeAgent([agent.Result("success", True, "Done.", None, 0.1, 3, 9, denied)])
    said = []
    pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 1, newest=True), said.append, lambda _: None)
    item = state.find("notes", "c")
    report = (notes.home / "runs" / item.report).read_text(encoding="utf-8")
    assert "## Tool calls the sandbox refused\n\n- mcp__orbit__merge_entities: {'keep': 'x'}" in report
    assert "the sandbox refused 1 tool call" in said[-1]


def test_what_glean_s_checks_left_is_kept_in_the_report(setup):
    cfg, notes, state = setup
    left = ["lea@acme.example, which log_interaction listed under unknown_addresses: resolve and link it"]
    sent = [["sift reindex wasn't called", left[0]], left]
    answer = agent.Result("success", True, "Done.", None, 0.1, 3, 9, verdict="imported", checks=left, sent_back=sent)
    pipeline.run(cfg, notes, state, FakeAgent([answer]), pipeline.choose(state, notes, 1, newest=True), print, print)
    report = (notes.home / "runs" / state.find("notes", "c").report).read_text(encoding="utf-8")
    assert "followups: 2\nchecks_left: 1\n" in report
    assert report.endswith(
        f"## glean's checks\n\n- sent back 1: sift reindex wasn't called; {left[0]}\n- sent back 2: {left[0]}\n"
        f"- still left: {left[0]}\n"
    )


def test_what_glean_s_checks_found_and_the_run_did_is_in_the_report_too(setup):
    cfg, notes, state = setup
    answer = agent.Result("success", True, "Done.", None, 0.1, 3, 9, verdict="imported", sent_back=[["x"]])
    pipeline.run(cfg, notes, state, FakeAgent([answer]), pipeline.choose(state, notes, 1, newest=True), print, print)
    report = (notes.home / "runs" / state.find("notes", "c").report).read_text(encoding="utf-8")
    assert report.endswith("## glean's checks\n\n- sent back 1: x\n")


def test_a_dry_run_shows_the_message_and_runs_nothing(setup):
    cfg, notes, state = setup
    fake, out = FakeAgent(), []
    assert (
        pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 1, newest=True), print, out.append, True)
        == {}
    )
    assert (fake.prepared, fake.runs) == (0, [])
    assert out[0].startswith("Here is the next note.")
    assert state.find("notes", "c").status == "new"


def test_nothing_to_do_needs_no_agent(setup):
    cfg, notes, state = setup
    fake = FakeAgent()
    assert pipeline.run(cfg, notes, state, fake, [], print, print) == {}
    assert fake.prepared == 0


def test_an_item_whose_document_fails(setup):
    cfg, notes, state = setup
    item = state.find("notes", "a")
    (notes.archive / item.path).write_text("unreadable", encoding="utf-8")
    out = []
    counts = pipeline.run(cfg, notes, state, FakeAgent(), [item], print, out.append)
    assert counts == {"failed": 1}
    assert state.find("notes", "a").reason == "its document couldn't be written: ValueError: can't read it"


def test_ctrl_c_leaves_the_item_as_it_was(setup):
    cfg, notes, state = setup
    fake = FakeAgent([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 1, newest=True), print, print)
    item = state.find("notes", "c")
    assert (item.status, item.attempts, item.reason) == ("new", 1, "stopped with Ctrl+C")


def test_one_glean_at_a_time(tmp_path):
    home = tmp_path / "home"
    with pipeline.lock(home), pytest.raises(GleanError, match="another glean"), pipeline.lock(home):
        pass
    with pipeline.lock(tmp_path / "home"):
        pass


def test_a_connector_needs_no_checks_of_its_own(setup):
    _, notes, _ = setup
    assert notes.check() == []


def test_named_items_go_oldest_first_by_the_moment(setup):
    _, notes, state = setup
    notes.source = {
        "early": ("2026-10-14T09:00:00+02:00", "Early", "x"),  # 07:00 UTC
        "late": ("2026-10-14T08:30:00+00:00", "Late", "y"),  # 08:30 UTC, though its text sorts first
    }
    notes.download(state)
    assert [i.key for i in pipeline.choose(state, notes, ids=["late", "early"])] == ["early", "late"]


def test_any_failure_to_write_a_document_fails_only_that_item(setup, monkeypatch):
    cfg, notes, state = setup
    real = notes.document

    def document(item):
        if item.key == "a":
            raise RuntimeError("a bug in the connector")
        return real(item)

    monkeypatch.setattr(notes, "document", document)
    out = []
    counts = pipeline.run(cfg, notes, state, FakeAgent(), pipeline.choose(state, notes, 3), print, out.append)
    assert counts == {"failed": 1, "done": 2}
    assert state.find("notes", "a").reason == "its document couldn't be written: RuntimeError: a bug in the connector"


def test_a_huge_item_reaches_the_agent_cut(setup, monkeypatch):
    _, notes, state = setup
    monkeypatch.setattr(pipeline, "MAX_ITEM_CHARS", 1000)
    item = state.find("notes", "a")
    (notes.archive / item.path).write_text("word " * 5000, encoding="utf-8")
    doc = notes.document(item)
    text = pipeline.message(notes, item, doc)
    assert len(text) < 2500
    assert f"[cut here: the note has {len(doc.text):,} characters; all of them are in its Markdown file]" in text
    assert text.endswith(">\n")


def test_the_prompt_names_the_collections_that_hold_the_files(setup):
    cfg, notes, _ = setup
    prompt = pipeline.system_prompt(cfg, notes, {"archive": "data", "vault": "data_orbit"})
    assert 'sift collection "data"' in prompt
    assert 'the collection "data_orbit"' in prompt


@pytest.mark.parametrize("verdict", ["skipped", "spam"])
def test_what_the_agent_judges_not_worth_importing_is_skipped(setup, verdict):
    cfg, notes, state = setup
    answer = agent.Result("success", True, "Nothing to record.", None, 0.01, 1, 9, verdict=verdict, summary="an alert")
    out = []
    counts = pipeline.run(
        cfg, notes, state, FakeAgent([answer]), pipeline.choose(state, notes, 1, newest=True), print, out.append
    )
    assert counts == {"skipped": 1}
    item = state.find("notes", "c")
    assert (item.status, item.reason, item.attempts) == ("skipped", f"agent: {verdict}: an alert", 1)
    assert out[0] == f"skipped {item.id}  2026-10-03  Third  (1 turn, $0.01: {verdict}: an alert)"


def test_an_import_says_what_was_imported(setup):
    cfg, notes, state = setup
    answer = agent.Result("success", True, "Done.", None, 0.2, 9, 9, verdict="imported", summary="Hans's offer")
    out = []
    pipeline.run(
        cfg, notes, state, FakeAgent([answer]), pipeline.choose(state, notes, 1, newest=True), print, out.append
    )
    assert out[0].endswith("(9 turns, $0.20: Hans's offer)")
    assert state.find("notes", "c").reason is None


def test_an_item_included_on_purpose_says_so(setup):
    _, notes, state = setup
    item = state.set_status(state.find("notes", "a"), "skipped", "bulk: List-Unsubscribe")
    text = pipeline.message(notes, item, notes.document(item))
    assert "- It was included on purpose, though skipped before (bulk: List-Unsubscribe):" in text
    assert 'log it as kind "automated"' in text


def entries(*keys):
    """(id, keys) pairs, oldest first, standing in for (item, document)."""
    return [(f"i{n}", frozenset(k)) for n, k in enumerate(keys)]


def test_unrelated_items_may_run_at_once():
    s = pipeline.Schedule(entries({"a"}, {"b"}, {"c"}), lambda e: e[1])
    assert [e[0] for e in s.take(2)] == ["i0", "i1"]
    assert [e[0] for e in s.take(5)] == ["i2"]
    assert s.take(5) == []


def test_unrelated_items_go_first():
    s = pipeline.Schedule(entries({"hans"}, {"lea"}, {"hans", "lea"}, {"anna"}), lambda e: e[1])
    assert [e[0] for e in s.take(3)] == ["i0", "i1", "i3"]  # i2 is related to both before it


def test_no_slot_idles_while_items_wait():
    # A pool: whichever run ends, the next item starts, related or not, so that every worker stays busy.
    s = pipeline.Schedule(entries({"hans"}, {"hans"}, {"hans"}), lambda e: e[1])
    assert [e[0] for e in s.take(2)] == ["i0", "i1"]
    s.finish(("i0", frozenset({"hans"})))
    assert [e[0] for e in s.take(1)] == ["i2"]


def test_a_waiting_item_holds_back_later_related_ones():
    s = pipeline.Schedule(entries({"a"}, {"a", "b"}, {"b"}, {"c"}), lambda e: e[1])
    assert [e[0] for e in s.take(2)] == ["i0", "i3"]  # i2 shares b with i1, which waits for i0
    assert [e[0] for e in s.take(2)] == ["i1", "i2"]


def test_items_without_keys_never_wait():
    s = pipeline.Schedule(entries(set(), set()), lambda e: e[1])
    assert len(s.take(2)) == 2


class Concurrent(FakeAgent):
    """Runs that wait a moment for company, and note who ran beside whom."""

    def __init__(self):
        super().__init__()
        self.active, self.most, self.events, self.lock = 0, 0, [], threading.Lock()
        self.company = threading.Event()

    def run(self, prompt, message, transcript, addresses=()):
        key = message.split("# ")[1].split("\n")[0]  # the note's title
        with self.lock:
            self.active += 1
            self.most = max(self.most, self.active)
            self.events.append(("start", key))
            if self.active > 1:
                self.company.set()
        self.company.wait(timeout=2)
        result = super().run(prompt, message, transcript, addresses)
        with self.lock:
            self.active -= 1
            self.events.append(("end", key))
        return result


def related(notes, keys):
    real = notes.document
    return lambda item: dataclasses.replace(real(item), related=frozenset(keys.get(item.key, ())))


def test_unrelated_items_run_side_by_side(setup):
    cfg, notes, state = setup
    fake = Concurrent()
    counts = pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), print, print, parallel=3)
    assert counts == {"done": 3}
    assert fake.most >= 2


def test_related_items_wait_while_unrelated_ones_can_run(setup, monkeypatch):
    cfg, notes, state = setup
    monkeypatch.setattr(notes, "document", related(notes, {"a": {"hans"}, "c": {"hans"}}))
    fake = Concurrent()
    pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), print, print, parallel=2)
    assert set(fake.events[:2]) == {("start", "First"), ("start", "Second")}  # b beside a; c, related to a, waits


class Crowd(FakeAgent):
    """Runs that end once `n` run at once, or, if they never do, after two seconds."""

    def __init__(self, n):
        super().__init__()
        self.barrier, self.together = threading.Barrier(n, timeout=2), True

    def run(self, prompt, message, transcript, addresses=()):
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            self.together = False
        return super().run(prompt, message, transcript, addresses)


def test_every_worker_stays_busy_while_items_wait(setup, monkeypatch):
    cfg, notes, state = setup
    monkeypatch.setattr(notes, "document", related(notes, {"a": {"hans"}, "b": {"hans"}, "c": {"hans"}}))
    fake = Crowd(3)
    assert pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), print, print, parallel=3) == {
        "done": 3
    }
    assert fake.together


def test_ctrl_c_stops_every_run(setup):
    cfg, notes, state = setup

    class Stoppable(FakeAgent):
        stopped = False

        def run(self, prompt, message, transcript, addresses=()):
            if "# First" in message:
                time.sleep(0.2)
                raise KeyboardInterrupt
            while not self.stopped:
                time.sleep(0.01)
            return agent.Result("crashed", False, reason="killed")

        def stop(self):
            self.stopped = True

    fake = Stoppable()
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), print, print, parallel=3)
    assert fake.stopped
    for key in "abc":
        item = state.find("notes", key)
        assert (item.status, item.reason) == ("new", "stopped with Ctrl+C")


TURNS = agent.Result("error_max_turns", False, reason="Reached maximum number of turns (60)")


def test_a_batch_whose_runs_keep_going_wrong_is_stopped(setup, monkeypatch):
    # Hundreds of runs failing alike cost time and money for nothing: the batch stops, and says why.
    cfg, notes, state = setup
    monkeypatch.setattr(pipeline, "TROUBLE_WINDOW", 2)
    monkeypatch.setattr(pipeline, "TROUBLE_LIMIT", 2)
    fake = FakeAgent([TURNS, TURNS])
    with pytest.raises(pipeline.Tripped, match=r"2 of the last 2 runs went wrong, most often: Reached maximum"):
        pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), print, print)
    assert len(fake.runs) == 2
    assert (state.find("notes", "c").status, state.find("notes", "c").attempts) == ("new", 0)  # never started


def test_runs_that_go_wrong_now_and_then_dont_stop_the_batch(setup, monkeypatch):
    cfg, notes, state = setup
    monkeypatch.setattr(pipeline, "TROUBLE_WINDOW", 2)
    monkeypatch.setattr(pipeline, "TROUBLE_LIMIT", 2)
    fake = FakeAgent([TURNS, agent.Result("success", True, "Imported.", None, 0.1, 5, 99), TURNS])
    assert pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 3), print, print) == {
        "failed": 2, "done": 1}  # fmt: skip


def test_a_run_that_went_wrong_without_saying_why_is_named_by_its_outcome(setup, monkeypatch):
    cfg, notes, state = setup
    monkeypatch.setattr(pipeline, "TROUBLE_WINDOW", 1)
    monkeypatch.setattr(pipeline, "TROUBLE_LIMIT", 1)
    blank = agent.Result("crashed", False, reason=" \n ")
    with pytest.raises(pipeline.Tripped, match=r"most often: crashed \(1 times\)"):
        pipeline.run(cfg, notes, state, FakeAgent([blank]), pipeline.choose(state, notes, 1), print, print)


def test_work_left_undone_and_refused_calls_count_as_going_wrong(setup, monkeypatch):
    cfg, notes, state = setup
    monkeypatch.setattr(pipeline, "TROUBLE_WINDOW", 2)
    monkeypatch.setattr(pipeline, "TROUBLE_LIMIT", 2)
    left = agent.Result("success", True, "Done.", None, 0.1, 3, 9, verdict="imported", checks=["x"])
    refused = agent.Result("success", True, "Done.", None, 0.1, 3, 9, denied=[{"tool_name": "Bash"}])
    with pytest.raises(pipeline.Tripped, match="most often: glean's checks left work undone"):
        pipeline.run(cfg, notes, state, FakeAgent([left, refused]), pipeline.choose(state, notes, 3), print, print)


def test_a_run_that_cannot_start_stops_the_others_and_is_named(setup):
    cfg, notes, state = setup
    fake = FakeAgent([GleanError("can't start Claude Code (claude): no such file")])
    with pytest.raises(GleanError, match="can't start"):
        pipeline.run(cfg, notes, state, fake, pipeline.choose(state, notes, 1, newest=True), print, print)
    assert state.find("notes", "c").reason == "stopped: can't start Claude Code (claude): no such file"
