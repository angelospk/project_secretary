"""Membership judge: parsing and abstain-on-failure."""

from __future__ import annotations

from secretary.labeler.judge import membership_judge, parse_membership
from secretary.labeler.taxonomy import Category

CAT = Category(key="notif", description="delivery", label="notif", examples=())


def test_parse_membership_reads_first_verdict():
    assert parse_membership("YES, it clearly fits") is True
    assert parse_membership("NO — different area") is False
    assert parse_membership("yes") is True


def test_parse_membership_abstains_on_garbage():
    assert parse_membership("maybe?") is None
    assert parse_membership("") is None


def test_membership_judge_abstains_when_backend_raises():
    def boom(prompt):
        raise RuntimeError("network down")

    judge = membership_judge(boom)
    assert judge("title", "body", CAT) is None


def test_membership_judge_returns_verdict():
    judge = membership_judge(lambda prompt: "YES")
    assert judge("title", "body", CAT) is True
