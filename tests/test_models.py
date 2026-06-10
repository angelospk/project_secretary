from secretary.github.models import (
    Comment,
    Issue,
    PullRequest,
    closing_refs,
    parent_number_from_issue_url,
)


def test_closing_refs_dedup_and_keywords():
    body = "This closes #12 and Fixes #3. Also resolves #12 again. Mentions #99 (no kw)."
    assert closing_refs(body) == [12, 3]


def test_closing_refs_none():
    assert closing_refs(None) == []
    assert closing_refs("no refs here") == []


def test_parent_number_from_issue_url():
    url = "https://api.github.com/repos/schemalabz/opencouncil/issues/42"
    assert parent_number_from_issue_url(url) == 42


def test_issue_from_api():
    raw = {
        "number": 5,
        "title": "Bug",
        "body": "desc",
        "state": "open",
        "user": {"login": "alice"},
        "labels": [{"name": "bug"}, {"name": "ui"}],
        "html_url": "http://x/5",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "closed_at": None,
    }
    issue = Issue.from_api(raw, "owner/app")
    assert issue.repo == "owner/app"
    assert issue.number == 5
    assert issue.author == "alice"
    assert issue.labels == ["bug", "ui"]
    assert issue.closed_at is None


def test_pull_request_from_api_extracts_links():
    raw = {
        "number": 20,
        "title": "Fix",
        "body": "closes #5",
        "state": "closed",
        "user": {"login": "bob"},
        "labels": [],
        "html_url": "http://x/20",
        "head": {"ref": "feat/x"},
        "base": {"ref": "main"},
        "merged_at": "2024-02-01T00:00:00Z",
    }
    pr = PullRequest.from_api(raw, "owner/app")
    assert pr.repo == "owner/app"
    assert pr.head_ref == "feat/x"
    assert pr.base_ref == "main"
    assert pr.linked_issues == [5]
    assert pr.merged_at is not None


def test_comment_from_api():
    raw = {
        "id": 9001,
        "issue_url": "https://api.github.com/repos/o/r/issues/7",
        "user": {"login": "carol"},
        "body": "hi",
        "html_url": "http://x/c",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    c = Comment.from_api(raw, "owner/app")
    assert c.repo == "owner/app"
    assert c.gh_id == 9001
    assert c.parent_number == 7
    assert c.author == "carol"
