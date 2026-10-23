"""The benchmark's logic (experiments/bench.py): picking emails, comparing two keys, reading a vault, grading."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "experiments"))

import bench


def row(n, thread=None, **fields):
    found = {"id": f"e{n:03}", "date": f"2017-01-{n:02}T00:00:00", "title": "Hallo", "thread": thread or f"t{n}"}
    return found | {"outbound": False, "recipients": 1, "attachments": 0, "status": "new", "skipped_by": None} | fields


def test_threads_come_whole_and_every_email_once_oldest_first():
    rows = [row(n, thread="long") for n in range(1, 5)] + [row(n, outbound=True) for n in range(5, 15)]
    rows += [row(15, title="WG: Termin"), row(16, skipped_by="rule", status="skipped")]
    chosen = bench.pick(rows, {"thread": 3, "outbound": 4, "forward": 2, "rule_skipped": 1}, seed=3)
    strata = Counter(s for _, s in chosen)
    assert strata == {"thread": 4, "outbound": 4, "forward": 1, "rule_skipped": 1}  # the thread whole, past its quota
    ids = [r["id"] for r, _ in chosen]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    assert chosen == bench.pick(rows, {"thread": 3, "outbound": 4, "forward": 2, "rule_skipped": 1}, seed=3)


def test_short_threads_are_no_thread():
    chosen = bench.pick([row(1, thread="x"), row(2, thread="x")], {"thread": 5, "new": 5})
    assert {s for _, s in chosen} == {"new"}


def test_one_person_spelled_two_ways():
    assert bench.same_person({"name": "Jürg Müller", "emails": []}, {"name": "jurg  MULLER", "emails": ["j@x.ch"]})
    assert bench.same_person({"name": "J. M.", "emails": ["J@X.ch"]}, {"name": "Jürg", "emails": ["j@x.ch"]})
    assert not bench.same_person({"name": "", "emails": []}, {"name": "", "emails": []})
    assert bench.same_person({"name": "Frau Keller", "emails": []}, {"name": "Anna Keller", "emails": []})
    assert not bench.same_person({"name": "Herr Frei", "emails": []}, {"name": "Lea Frei Meier", "emails": []})
    assert not bench.same_person({"name": "Anna", "emails": ["a@x.ch"]}, {"name": "Anne", "emails": ["b@x.ch"]})


def key(**fields):
    return {"verdict": "import", "people": [], "orgs": [], "tasks": [], "owner_facts": [], "facts": []} | fields


def test_two_keys_that_agree():
    hans = {"name": "Hans Müller", "emails": ["hans@acme.ch"], "role": "from"}
    a = key(people=[hans], orgs=[{"name": "Acme AG"}], facts=[{"about": "Hans", "fact": "x"}])
    b = key(people=[hans | {"name": "Hans Mueller"}], orgs=[{"name": "acme ag"}])  # facts are worded freely
    assert bench.differences(a, b) == []


def test_how_two_keys_differ():
    assert bench.differences(key(), key(verdict="skip")) == ["verdict: import or skip"]
    lea = {"name": "Lea Frei", "emails": [], "role": "mentioned"}
    found = bench.differences(key(people=[lea], tasks=[{}]), key(orgs=[{"name": "Acme"}]))
    assert found == ["mentioned: only one names Lea Frei", "orgs: only one names acme", "tasks: 1 or 0"]


ORBIT_NOTE = """---
type: person
id: per_01
name: Hans Müller
emails:
  - hans@acme.example
phones:
  - "+41 79 684 33 15"
org: "[[Acme AG]]"
aliases: []
me: true
---
## Notes
"""


def test_a_note_s_front_matter_as_orbit_writes_it():
    fields = bench.front_matter(ORBIT_NOTE)
    assert fields["emails"] == ["hans@acme.example"]
    assert fields["phones"] == ["+41 79 684 33 15"]
    assert (fields["org"], fields["aliases"], fields["me"]) == ("Acme AG", [], "true")
    assert bench.front_matter("no front matter") == {}


def test_a_vault_s_notes(tmp_path):
    (tmp_path / "01_People").mkdir()
    (tmp_path / "01_People" / "Hans Müller.md").write_text(ORBIT_NOTE, encoding="utf-8")
    (tmp_path / "loose.md").write_text("# just text\n", encoding="utf-8")
    (tmp_path / ".trash").mkdir()  # where orbit puts a merged note
    (tmp_path / ".trash" / "Hans Müller (2).md").write_text(ORBIT_NOTE, encoding="utf-8")
    notes = bench.vault_notes(tmp_path)
    assert list(notes) == ["Hans Müller"]
    assert notes["Hans Müller"][1].strip().startswith("## Notes")


def test_organizations_by_name_variant_or_domain():
    note = {"name": "Beta Group AG", "aliases": ["BG"], "domains": ["beta-group.example"]}
    assert bench.same_org({"name": "Beta"}, note)
    assert bench.same_org({"name": "BG"}, note)
    assert bench.same_org({"name": "Group Careers", "domains": ["Beta-Group.example"]}, note)
    assert not bench.same_org({"name": "acm"}, {"name": "Acme AG"})


def test_records_of_one_person_cluster_through_each_other():
    people = [{"name": "Hans", "emails": ["h@x.ch"]}, {"name": "Hans Müller", "emails": ["h@x.ch"]},
              {"name": "hans muller", "emails": []}, {"name": "Lea", "emails": []}]  # fmt: skip
    assert sorted(map(sorted, bench.clusters(people))) == [[0, 1, 2], [3]]


def test_what_a_run_s_answer_means_for_the_key():
    assert [bench.recorded(v) for v in ("imported", "already_imported", "skipped", "spam", None)] == [
        "import", "import", "skip", "spam", None]  # fmt: skip


def test_recall_and_precision_of_links():
    counts = Counter()
    wanted = [{"name": "Hans", "emails": []}, {"name": "Lea", "emails": []}]
    bench.match(counts, "people", wanted, [{"name": "Hans"}, {"name": "Marco"}, {"name": "hans"}], bench.same_person)
    assert (counts["people_expected"], counts["people_found"]) == (2, 1)
    assert (counts["people_linked"], counts["people_right"]) == (3, 2)


def test_the_grade_counts_people_across_the_set():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    keys = {
        "a": key(people=[{"name": "Hans", "emails": ["h@x.ch"], "role": "from"}], owner_addresses=["me@x.ch"]),
        "b": key(
            people=[
                {"name": "Hans Müller", "emails": ["h@x.ch"], "role": "to"},
                {"name": "Lea", "emails": ["l@x.ch"], "role": "cc"},
            ]
        ),
        "c": key(verdict="skip", people=[{"name": "Shop", "emails": [], "role": "from"}]),
    }
    notes = [{"name": "Hans Müller", "emails": ["h@x.ch"]}, {"name": "Hans", "emails": []},
             {"name": "Tobias W", "emails": ["me@x.ch"]}]  # fmt: skip
    confusion = Counter({("import", "import"): 1, ("import", "skip"): 1, ("skip", "skip"): 1})
    grade = bench.summary(Counter(), confusion, keys, items, notes)
    assert grade["people"] == {"expected": 2, "notes": 3, "missing": 1, "duplicated": 1, "asked": 0,
                               "owner_as_person": 1, "unexpected": 1}  # fmt: skip
    assert grade["people_duplicated"] == [["Hans Müller", "Hans"]]
    notes[1]["review_status"] = "needs_review"  # created provisional, with a review for the owner
    asked = bench.summary(Counter(), confusion, keys, items, notes)["people"]
    assert (asked["duplicated"], asked["asked"]) == (0, 1)
    assert grade["verdict_accuracy"] == 0.667
    assert grade["tasks"] == {"recall": None, "precision": None}  # nothing to count: no number
    assert "| verdict_accuracy | 0.667 |" in bench.markdown("x", grade | {"remarks": ["one"]})
