"""Labeler action decisions, the human-veto guard, and run_labeler orchestration."""

from __future__ import annotations

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.labeler import apply as apply_mod
from secretary.labeler.apply import decide_action, is_blacklisted
from secretary.labeler.centroids import Centroid
from secretary.labeler.taxonomy import Category, Taxonomy


def test_decide_action_covers_every_band():
    assert decide_action("silence", None, "auto") == "skip"
    assert decide_action("accept", None, "auto") == "apply"
    assert decide_action("accept", None, "suggest") == "suggest"
    assert decide_action("review", True, "auto") == "apply"
    assert decide_action("review", None, "auto") == "suggest"   # judge abstained
    assert decide_action("review", False, "auto") == "suggest"  # judge said no
    assert decide_action("review", True, "suggest") == "suggest"


def _patch_kv(monkeypatch, kv: dict) -> None:
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))


def test_human_removed_label_is_vetoed_permanently(monkeypatch):
    kv = {"label_applied:5:notif": {"dist": 0.1}}  # the secretary applied it earlier
    _patch_kv(monkeypatch, kv)
    # the label is no longer on the issue → a human removed it.
    assert is_blacklisted(None, "o/r", 5, "notif", ["other"]) is True
    assert kv["label_veto:5:notif"] is True  # recorded
    # the veto sticks even if the label reappears later.
    assert is_blacklisted(None, "o/r", 5, "notif", ["notif"]) is True


def test_label_still_present_is_not_vetoed(monkeypatch):
    kv = {"label_applied:5:notif": {"dist": 0.1}}
    _patch_kv(monkeypatch, kv)
    assert is_blacklisted(None, "o/r", 5, "notif", ["notif"]) is False


# --- run_labeler orchestration -------------------------------------------------

class StubClient:
    def __init__(self, create_number: int = 900):
        self.added: list[tuple[int, list[str]]] = []
        self.created_title: str | None = None
        self.updated = False
        self._create_number = create_number

    def add_labels(self, number, labels):
        self.added.append((number, labels))
        return []

    def create_issue(self, title, body, labels=None):
        self.created_title = title
        return {"number": self._create_number, "body": body}

    def get_issue(self, number):
        return {"body": ""}

    def update_issue_body(self, number, body):
        self.updated = True
        return {}


def _centroids():
    return [Centroid("notif", "notif", [1.0, 0.0]),
            Centroid("trans", "trans", [0.0, 1.0])]


def _taxonomy():
    return Taxonomy(
        (Category("notif", "delivery", "notif", ()),
         Category("trans", "speaker", "trans", ())),
        "h",
    )


def _run(monkeypatch, rows, *, mode="auto", apply=False, kv=None, client=None,
         judge=None, accept=0.35, review=0.5):
    kv = {} if kv is None else kv
    monkeypatch.setattr(apply_mod, "load_taxonomy", lambda path: _taxonomy())
    monkeypatch.setattr(apply_mod, "build_centroids", lambda db, e, r, t: _centroids())
    monkeypatch.setattr(db_repo, "issues_for_labeling", lambda db, repo: rows)
    _patch_kv(monkeypatch, kv)
    settings = Settings(github_repo="o/r", labeler_mode=mode,
                        labeler_accept=accept, labeler_review=review)
    results = apply_mod.run_labeler(None, None, client, settings, "o/r",
                                    apply=apply, judge=judge)
    return results, kv


def _row(number, vec, labels=None):
    return {"number": number, "title": f"#{number}", "body": "",
            "labels": labels or [], "embedding": vec}


def test_auto_mode_applies_a_confident_label(monkeypatch):
    client = StubClient()
    results, kv = _run(monkeypatch, [_row(5, [1.0, 0.02])],
                       mode="auto", apply=True, client=client)
    assert client.added == [(5, ["notif"])]
    assert results[0].action == "applied"
    assert kv.get("label_applied:5:notif") is not None


def test_already_taxonomy_labeled_issue_is_skipped(monkeypatch):
    results, _ = _run(monkeypatch, [_row(5, [1.0, 0.02], labels=["notif"])],
                      mode="auto", apply=True, client=StubClient())
    assert results == []


def test_vetoed_pair_is_reported_but_not_applied(monkeypatch):
    client = StubClient()
    kv = {"label_veto:5:notif": True}
    results, _ = _run(monkeypatch, [_row(5, [1.0, 0.02])],
                      mode="auto", apply=True, kv=kv, client=client)
    assert client.added == []
    assert results[0].action == "vetoed"


def test_suggest_mode_posts_a_report_issue(monkeypatch):
    client = StubClient(create_number=900)
    results, kv = _run(monkeypatch, [_row(5, [1.0, 0.02])],
                       mode="suggest", apply=True, client=client)
    assert results[0].action == "suggested"
    assert client.added == []  # suggest mode never applies labels
    assert client.created_title == "Label suggestions"
    assert client.updated
    assert kv.get("label_suggestions_issue") == 900


def test_review_band_applies_only_when_the_judge_confirms(monkeypatch):
    client = StubClient()
    results, _ = _run(monkeypatch, [_row(6, [1.0, 1.0])], mode="auto", apply=True,
                      client=client, judge=lambda t, b, cat: True,
                      accept=0.1, review=0.5)
    assert results[0].action == "applied"
    assert client.added == [(6, [results[0].label])]


def test_review_band_abstain_downgrades_to_suggestion(monkeypatch):
    client = StubClient()
    results, _ = _run(monkeypatch, [_row(6, [1.0, 1.0])], mode="auto", apply=True,
                      client=client, judge=lambda t, b, cat: None,
                      accept=0.1, review=0.5)
    assert results[0].action == "suggested"
    assert client.added == []


# --- single-issue scoping (the webhook path) ----------------------------------

def _run_scoped(monkeypatch, rows, numbers, *, mode="auto", apply=True,
                kv=None, client=None):
    kv = {} if kv is None else kv
    monkeypatch.setattr(apply_mod, "load_taxonomy", lambda path: _taxonomy())
    monkeypatch.setattr(apply_mod, "build_centroids", lambda db, e, r, t: _centroids())
    monkeypatch.setattr(db_repo, "issues_for_labeling", lambda db, repo: rows)
    _patch_kv(monkeypatch, kv)
    settings = Settings(github_repo="o/r", labeler_mode=mode)
    results = apply_mod.run_labeler(None, None, client, settings, "o/r",
                                    apply=apply, numbers=numbers)
    return results, kv


def test_numbers_filter_classifies_only_the_named_issue(monkeypatch):
    client = StubClient()
    rows = [_row(5, [1.0, 0.02]), _row(8, [1.0, 0.02])]
    results, _ = _run_scoped(monkeypatch, rows, {5}, client=client)
    assert client.added == [(5, ["notif"])]          # only #5 acted on
    assert [r.number for r in results] == [5]


def test_scoped_suggest_run_does_not_rewrite_shared_report(monkeypatch):
    client = StubClient()
    rows = [_row(5, [1.0, 0.02])]
    results, kv = _run_scoped(monkeypatch, rows, {5}, mode="suggest", client=client)
    assert results[0].action == "suggested"
    assert client.created_title is None              # shared report untouched
    assert client.updated is False
    assert "label_suggestions_issue" not in kv
