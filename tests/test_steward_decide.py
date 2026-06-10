"""Pure steward decisions: PR-derived status, forward-only, priority buckets."""

from __future__ import annotations

from secretary.steward.decide import (
    desired_status_from_prs,
    next_status,
    priority_bucket,
)


def test_desired_status_prefers_merged_then_open():
    assert desired_status_from_prs(["open", "merged"]) == "Done"
    assert desired_status_from_prs(["open"]) == "In Progress"
    assert desired_status_from_prs(["closed"]) is None   # closed-unmerged = no signal
    assert desired_status_from_prs([]) is None


def test_next_status_is_forward_only():
    assert next_status(None, "In Progress") == "In Progress"
    assert next_status("Todo", "In Progress") == "In Progress"
    assert next_status("In Progress", "Done") == "Done"
    assert next_status("Done", "In Progress") is None   # never demote
    assert next_status("In Progress", "In Progress") is None  # no redundant write
    assert next_status("Done", None) is None


def test_priority_bucket_quartiles():
    assert priority_bucket(0, 8) == "P1"
    assert priority_bucket(2, 8) == "P2"
    assert priority_bucket(4, 8) == "P3"
    assert priority_bucket(6, 8) == "P4"
    assert priority_bucket(0, 0) == "P4"  # degenerate
