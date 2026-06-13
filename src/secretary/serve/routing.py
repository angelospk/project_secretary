"""Map a verified GitHub webhook to a triage task — pure, socket-free.

`build_task` is the single source of the (event, action) → disposition table the
spec defines:

  issues       opened|reopened  -> full triage (ingest + enrich + labels)
  issues       edited           -> ingest-only
  issue_comment created|edited  -> ingest-only (comment)
  pull_request opened|reopened|edited|synchronize -> ingest-only (pr)
  everything else                -> ignored (caller responds 204)

A payload for an unconfigured repo, a missing repository, or an unknown
(event, action) returns None — the caller treats None as "ignore, 204".
"""

from __future__ import annotations

from dataclasses import dataclass

from secretary.config import normalize_repo


@dataclass(frozen=True)
class TriageTask:
    repo: str
    number: int
    action: str       # "triage" | "ingest"
    raw: dict         # the object to feed the ingest pipeline
    raw_kind: str     # "issue" | "pr" | "comment"


_ISSUE_TRIAGE = {"opened", "reopened"}
_ISSUE_INGEST = {"edited"}
_COMMENT_ACTIONS = {"created", "edited"}
_PR_ACTIONS = {"opened", "reopened", "edited", "synchronize"}


def _repo_of(payload: dict, allowed_repos: set[str]) -> str | None:
    full = (payload.get("repository") or {}).get("full_name")
    if not full:
        return None
    try:
        repo = normalize_repo(full)
    except ValueError:
        return None
    return repo if repo in allowed_repos else None


def build_task(event: str, payload: dict, allowed_repos: set[str]) -> TriageTask | None:
    """Resolve (event, action, payload) into a TriageTask, or None to ignore."""
    repo = _repo_of(payload, allowed_repos)
    if repo is None:
        return None
    action = payload.get("action") or ""

    if event == "issues":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        if number is None:
            return None
        if action in _ISSUE_TRIAGE:
            return TriageTask(repo, int(number), "triage", issue, "issue")
        if action in _ISSUE_INGEST:
            return TriageTask(repo, int(number), "ingest", issue, "issue")
        return None

    if event == "issue_comment" and action in _COMMENT_ACTIONS:
        comment = payload.get("comment") or {}
        issue = payload.get("issue") or {}
        number = issue.get("number")
        if number is None or not comment:
            return None
        return TriageTask(repo, int(number), "ingest", comment, "comment")

    if event == "pull_request" and action in _PR_ACTIONS:
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        if number is None:
            return None
        # Wrap into the issues-listing shape pipeline.is_pull() expects.
        raw = {"number": int(number), "pull_request": pr}
        return TriageTask(repo, int(number), "ingest", raw, "pr")

    return None
