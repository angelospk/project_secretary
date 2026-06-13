"""Gardener against a live SurrealDB: a fixed-but-open pair, one comment over two runs.

Skipped automatically if no server is reachable (same gate as test_integration_db).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from secretary.config import Settings
from secretary.db import repo
from secretary.db.connection import surreal
from secretary.gardener import garden
from secretary.github.models import Issue, PullRequest

REPO = "owner/app"
NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)


def _settings(**kw) -> Settings:
    return Settings(
        github_repo=REPO,
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root", surreal_pass="root",
        surreal_ns="opencouncil", surreal_db="secretary_garden_itest",
        **kw,
    )


@pytest.fixture()
def db():
    settings = _settings()
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    try:
        for table in ("issue", "pr", "comment", "relates_to", "mentions",
                      "sync_state", "organizer_kv"):
            conn.query(f"REMOVE TABLE IF EXISTS {table};")
        repo.apply_schema(conn)
        yield conn
    finally:
        cm.__exit__(None, None, None)


class StubClient:
    def __init__(self):
        self.comments = []
        self.created = None
        self.body = ""

    def create_issue(self, title, body, labels=None):
        self.created = title
        return {"number": 999, "body": body}

    def get_issue(self, number):
        return {"body": self.body}

    def update_issue_body(self, number, body):
        self.body = body
        return {}

    def create_comment(self, number, body):
        self.comments.append((number, body))
        return {}


def test_fixed_but_open_pair_yields_one_comment_over_two_runs(db):
    # Issue #1 stayed open; merged PR #50 closes it (relates_to edge).
    repo.upsert_issue(db, Issue(repo=REPO, number=1, title="login fails", state="open"))
    pr = PullRequest(repo=REPO, number=50, title="fix login", state="closed",
                     merged_at=NOW, linked_issues=[1])
    repo.upsert_pr(db, pr)
    repo.relate(db, ("pr", REPO, 50), "relates_to", ("issue", REPO, 1))

    settings = _settings(gardener_mode="comment")
    client = StubClient()

    findings = garden.run_gardener(db, None, client, settings, REPO, now=NOW, apply=True)
    fixed = [f for f in findings if f.signal == "probably_fixed"]
    assert len(fixed) == 1 and fixed[0].issue == 1 and fixed[0].confidence == "confident"
    assert len(client.comments) == 1

    # Re-run: same evidence ⇒ no new comment (idempotent by (issue, signal, fingerprint)).
    garden.run_gardener(db, None, client, settings, REPO, now=NOW, apply=True)
    assert len(client.comments) == 1
