"""Drift fingerprint: a plan is rebuilt only when its members or config actually change."""

from __future__ import annotations

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.organizer import drift, plan as organizer_plan, writer as organizer_writer
from secretary.organizer.models import Item, ReleasePlan


def _settings(**overrides) -> Settings:
    return Settings(github_repo="o/r", **overrides)


def _member(number: int, *, updated: float = 100.0, state: str = "open", labels=None) -> Item:
    return Item(
        kind="issue", repo="o/r", number=number, title=f"#{number}", state=state,
        labels=list(labels or []), milestone="v1", updated_at_epoch=updated,
    )


# --- pure fingerprint --------------------------------------------------------

def test_fingerprint_is_stable_for_the_same_members():
    s = _settings()
    members = [_member(1), _member(2)]
    assert drift.plan_fingerprint(members, s) == drift.plan_fingerprint(members, s)


def test_fingerprint_is_order_independent():
    s = _settings()
    a = drift.plan_fingerprint([_member(1), _member(2)], s)
    b = drift.plan_fingerprint([_member(2), _member(1)], s)
    assert a == b


def test_fingerprint_changes_when_a_member_is_updated():
    s = _settings()
    before = drift.plan_fingerprint([_member(1, updated=100.0)], s)
    after = drift.plan_fingerprint([_member(1, updated=200.0)], s)
    assert before != after


def test_fingerprint_changes_when_labels_change():
    s = _settings()
    before = drift.plan_fingerprint([_member(1, labels=["bug"])], s)
    after = drift.plan_fingerprint([_member(1, labels=["bug", "p0"])], s)
    assert before != after


def test_fingerprint_changes_when_a_member_is_added_or_removed():
    s = _settings()
    one = drift.plan_fingerprint([_member(1)], s)
    two = drift.plan_fingerprint([_member(1), _member(2)], s)
    assert one != two


def test_fingerprint_changes_when_priority_weights_change():
    members = [_member(1)]
    a = drift.plan_fingerprint(members, _settings(priority_weights="react=1.0"))
    b = drift.plan_fingerprint(members, _settings(priority_weights="dep=1.0"))
    assert a != b


def test_judge_config_only_matters_when_the_judge_is_enabled():
    members = [_member(1)]
    off_a = drift.plan_fingerprint(members, _settings(judge_enabled=False, judge_rubric="A"))
    off_b = drift.plan_fingerprint(members, _settings(judge_enabled=False, judge_rubric="B"))
    assert off_a == off_b  # rubric is irrelevant while the judge is off

    on_a = drift.plan_fingerprint(members, _settings(judge_enabled=True, judge_rubric="A"))
    on_b = drift.plan_fingerprint(members, _settings(judge_enabled=True, judge_rubric="B"))
    assert on_a != on_b


# --- maintain_plan orchestration ---------------------------------------------

# A non-None stand-in for the GitHubClient: write_plan is monkeypatched so it is never
# dereferenced, but maintain_plan(write=True) now refuses a None client (fail-fast).
CLIENT = object()


class _Recorder:
    """Captures whether the expensive build/write path ran."""

    def __init__(self):
        self.built = 0
        self.wrote = 0

    def build(self, db, embedder, settings, repo, milestone, *, judge=None, members=None):
        self.built += 1
        return ReleasePlan(repo, milestone, members or [], [], [], [], [])

    def write_plan(self, client, db, settings, plan):
        self.wrote += 1
        return f"updated release plan in #1"


def _wire(monkeypatch, kv: dict, rec: _Recorder, members: list[dict]) -> None:
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set",
                        lambda db, repo, key, value: kv.__setitem__(key, value))
    monkeypatch.setattr(db_repo, "milestone_members",
                        lambda db, repo, milestone, *, include_native=False: members)
    monkeypatch.setattr(organizer_plan, "build", rec.build)
    monkeypatch.setattr(organizer_writer, "write_plan", rec.write_plan)


def _row(number: int, *, updated: str = "2026-01-01T00:00:00Z") -> dict:
    return {"kind": "issue", "repo": "o/r", "number": number, "title": f"#{number}",
            "state": "open", "labels": [], "milestone": "v1", "updated_at": updated}


def test_maintain_skips_the_build_when_nothing_changed(monkeypatch):
    rec = _Recorder()
    kv: dict = {}
    _wire(monkeypatch, kv, rec, [_row(1)])

    first = drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True)
    assert first.changed is True
    assert rec.built == 1 and rec.wrote == 1

    second = drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True)
    assert second.changed is False
    assert rec.built == 1 and rec.wrote == 1  # unchanged: no rebuild, no write


def test_maintain_rebuilds_when_a_member_changes(monkeypatch):
    rec = _Recorder()
    kv: dict = {}
    _wire(monkeypatch, kv, rec, [_row(1, updated="2026-01-01T00:00:00Z")])
    drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True)

    # member touched -> fingerprint differs -> rebuild
    _wire(monkeypatch, kv, rec, [_row(1, updated="2026-02-02T00:00:00Z")])
    again = drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True)
    assert again.changed is True
    assert rec.built == 2 and rec.wrote == 2


def test_force_rebuilds_even_when_unchanged(monkeypatch):
    rec = _Recorder()
    kv: dict = {}
    _wire(monkeypatch, kv, rec, [_row(1)])
    drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True)
    drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True, force=True)
    assert rec.built == 2


def test_dry_run_does_not_write_or_store_fingerprint(monkeypatch):
    rec = _Recorder()
    kv: dict = {}
    _wire(monkeypatch, kv, rec, [_row(1)])
    res = drift.maintain_plan(None, None, None, _settings(), "o/r", "v1", write=False)
    assert rec.built == 1 and rec.wrote == 0
    assert drift.fingerprint_key("v1") not in kv  # dry-run leaves no fingerprint
    assert res.plan is not None


def test_maintain_reports_no_members(monkeypatch):
    rec = _Recorder()
    _wire(monkeypatch, {}, rec, [])
    res = drift.maintain_plan(None, None, CLIENT, _settings(), "o/r", "v1", write=True)
    assert res.changed is False and rec.built == 0
    assert "no" in res.message.lower()


def test_plan_milestone_list_parses_and_dedupes():
    s = _settings(plan_milestones=" v2.1 , v2.2 ,v2.1, ")
    assert s.plan_milestone_list == ["v2.1", "v2.2"]
    assert _settings().plan_milestone_list == []
