"""Pure classification: nearest category and the accept/review/silence bands."""

from __future__ import annotations

from secretary.labeler.centroids import Centroid
from secretary.labeler.classify import classify_issue

CENTROIDS = [
    Centroid("notifications", "notifications", [1.0, 0.0]),
    Centroid("transcript", "transcript", [0.0, 1.0]),
]


def test_confident_match_is_accept_band():
    c = classify_issue(1, [1.0, 0.02], CENTROIDS, accept=0.35, review=0.5)
    assert c.category == "notifications"
    assert c.label == "notifications"
    assert c.band == "accept"


def test_borderline_match_is_review_band():
    # 45° from both centroids → cosine dist ≈ 0.293, inside (accept, review].
    c = classify_issue(2, [1.0, 1.0], CENTROIDS, accept=0.1, review=0.5)
    assert c.band == "review"
    assert c.category in {"notifications", "transcript"}


def test_far_match_is_silence_and_carries_no_category():
    c = classify_issue(3, [1.0, 1.0], CENTROIDS, accept=0.1, review=0.2)
    assert c.band == "silence"
    assert c.category is None
    assert c.label is None


def test_no_centroids_silences():
    c = classify_issue(4, [1.0, 0.0], [], accept=0.35, review=0.5)
    assert c.band == "silence"
    assert c.category is None
