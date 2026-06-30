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


def test_match_carries_runner_up_and_positive_margin():
    # Vector near the notifications centroid; transcript is the orthogonal runner-up.
    c = classify_issue(1, [1.0, 0.02], CENTROIDS, accept=0.35, review=0.5)
    assert c.runner_up == "transcript"
    assert c.margin is not None and c.margin > 0  # nearer category wins by a margin


def test_single_centroid_has_no_runner_up():
    one = [Centroid("notifications", "notifications", [1.0, 0.0])]
    c = classify_issue(1, [1.0, 0.0], one, accept=0.35, review=0.5)
    assert c.runner_up is None
    assert c.margin is None


def test_no_centroids_silences():
    c = classify_issue(4, [1.0, 0.0], [], accept=0.35, review=0.5)
    assert c.band == "silence"
    assert c.category is None
