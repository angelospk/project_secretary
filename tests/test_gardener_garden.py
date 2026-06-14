"""Gardener orchestration: one-finding-per-issue, dedupe, vetoes, write idempotence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from secretary.config import Settings
from secretary.db import repo as db_repo
from secretary.gardener import garden
from secretary.gardener.signals import KEEP_MARKER
from secretary.semantic.reranker import DUPLICATE, HISTORICAL_REFERENCE

NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)


def _issue(number, **kw):
    base = {"number": number, "title": f"#{number}", "body": "", "labels": [],
            "milestone": None, "reactions": 0, "comments_count": 0,
            "created_at": NOW - timedelta(days=400), "updated_at": NOW}
    base.update(kw)
    return base


def _related(number, category, state="open", repo="o/r", dist=0.2):
    return SimpleNamespace(number=number, category=category, state=state, repo=repo,
                           dist=dist, title=f"#{number}", confidence=0.8, kind="issue",
                           labels=[], milestone=None, signals=[])


def _settings(**kw):
    return Settings(github_repo="o/r", gardener_dormant_days=180, **kw)


def _patch_kv(monkeypatch, kv):
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: kv.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: kv.__setitem__(key, value))


def _collect(monkeypatch, open_issues, *, linked=None, related=None, judge=None):
    monkeypatch.setattr(db_repo, "open_issues", lambda db, repo: open_issues)
    monkeypatch.setattr(db_repo, "linked_prs",
                        lambda db, repo, number: (linked or {}).get(number, []))
    related_fn = lambda db, e, r, n: (related or {}).get(n, [])
    return garden.collect_findings(
        None, None, _settings(), "o/r", now=NOW, judge=judge, related_fn=related_fn)


# --- one finding per issue (strongest wins) -----------------------------------

def test_fixed_wins_over_dormant_for_same_issue(monkeypatch):
    old = _issue(1, updated_at=NOW - timedelta(days=300))  # also dormant-eligible
    findings = _collect(monkeypatch, [old], linked={
        1: [{"repo": "o/r", "number": 50, "via": "relates_to", "merged_at": NOW, "state": "closed"}]})
    assert len(findings) == 1 and findings[0].signal == "probably_fixed"


def test_dormant_when_no_stronger_signal(monkeypatch):
    findings = _collect(monkeypatch, [_issue(1, updated_at=NOW - timedelta(days=300))])
    assert [f.signal for f in findings] == ["dormant"]


# --- duplicate pair dedupe ----------------------------------------------------

def test_duplicate_pair_emitted_once(monkeypatch):
    a = _issue(1, reactions=1)
    b = _issue(2, reactions=9)  # survivor
    related = {1: [_related(2, DUPLICATE)], 2: [_related(1, DUPLICATE)]}
    findings = _collect(monkeypatch, [a, b], related=related)
    dups = [f for f in findings if f.signal == "probably_duplicate"]
    assert len(dups) == 1 and dups[0].issue == 1  # loser only, once


# --- superseded judge band ----------------------------------------------------

def test_superseded_borderline_consults_judge(monkeypatch):
    calls = []
    judge = lambda issue, ref: calls.append(ref["number"]) or True
    related = {1: [_related(80, HISTORICAL_REFERENCE, state="closed", dist=0.50)]}
    findings = _collect(monkeypatch, [_issue(1)], related=related, judge=judge)
    assert calls == [80]
    assert findings[0].signal == "probably_superseded" and findings[0].confidence == "confident"


# --- vetoes -------------------------------------------------------------------

def test_keep_marker_vetoes_all_signals(monkeypatch):
    kv = {}
    _patch_kv(monkeypatch, kv)
    f = _issue(1)
    findings = garden.apply_vetoes(
        None, "o/r",
        [SimpleNamespace(issue=1, signal="dormant")],
        {1: f"keep this open {KEEP_MARKER}"},
    )
    assert findings == []


def test_kv_veto_suppresses_one_pair(monkeypatch):
    kv = {"gardener_veto:1:dormant": True}
    _patch_kv(monkeypatch, kv)
    kept = garden.apply_vetoes(
        None, "o/r",
        [SimpleNamespace(issue=1, signal="dormant"),
         SimpleNamespace(issue=1, signal="probably_fixed")],
        {1: ""},
    )
    assert [f.signal for f in kept] == ["probably_fixed"]


# --- writes -------------------------------------------------------------------

class StubClient:
    def __init__(self):
        self.comments = []
        self.created = None
        self.updated_body = None

    def create_issue(self, title, body, labels=None):
        self.created = title
        return {"number": 777, "body": body}

    def get_issue(self, number):
        return {"body": self.updated_body or ""}

    def update_issue_body(self, number, body):
        self.updated_body = body
        return {}

    def create_comment(self, number, body):
        self.comments.append((number, body))
        return {}


def _run(monkeypatch, open_issues, *, mode, apply, kv=None, related=None, linked=None, client=None):
    kv = {} if kv is None else kv
    monkeypatch.setattr(db_repo, "open_issues", lambda db, repo: open_issues)
    monkeypatch.setattr(db_repo, "linked_prs",
                        lambda db, repo, number: (linked or {}).get(number, []))
    _patch_kv(monkeypatch, kv)
    settings = _settings(gardener_mode=mode)
    related_fn = lambda db, e, r, n: (related or {}).get(n, [])
    findings = garden.run_gardener(
        None, None, client, settings, "o/r", now=NOW, apply=apply, related_fn=related_fn)
    return findings, kv


def test_dry_run_writes_nothing(monkeypatch):
    client = StubClient()
    _run(monkeypatch, [_issue(1, updated_at=NOW - timedelta(days=300))],
         mode="report", apply=False, client=client)
    assert client.created is None and client.updated_body is None


def test_report_mode_creates_and_fills_managed_section(monkeypatch):
    client = StubClient()
    findings, kv = _run(monkeypatch, [_issue(1, updated_at=NOW - timedelta(days=300))],
                        mode="report", apply=True, client=client)
    assert client.created == "Backlog gardening"
    assert "oc-secretary" in client.updated_body  # managed section markers
    assert kv.get("gardener_issue") == 777
    assert client.comments == []  # report mode never comments


def test_comment_mode_posts_one_comment_per_finding_once(monkeypatch):
    client = StubClient()
    old = [_issue(1, updated_at=NOW - timedelta(days=300))]
    kv = {}
    _run(monkeypatch, old, mode="comment", apply=True, kv=kv, client=client)
    assert len(client.comments) == 1 and client.comments[0][0] == 1
    # second run with the same finding → no new comment (idempotent by fingerprint)
    _run(monkeypatch, old, mode="comment", apply=True, kv=kv, client=client)
    assert len(client.comments) == 1


def test_human_edited_report_is_left_alone(monkeypatch):
    client = StubClient()
    client.updated_body = "tampered <!-- oc-secretary:v1 issue=777 context=deadbeef -->\nx\n<!-- /oc-secretary -->"
    _run(monkeypatch, [_issue(1, updated_at=NOW - timedelta(days=300))],
         mode="report", apply=True, kv={"gardener_issue": 777}, client=client)
    # was_human_edited → the body is not rewritten (still the tampered one)
    assert client.updated_body.startswith("tampered")
